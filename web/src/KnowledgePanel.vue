<script setup>
import { computed, ref, watch } from "vue";
const props = defineProps([
  "api",
  "knowledge",
  "eventId",
  "incident",
  "pendingJobs",
  "parentBusy",
]);
const emit = defineEmits(["refresh", "notice", "error"]);
const busy = ref(false),
  attachment = ref(null),
  agreed = ref(false);
const cases = ref([]),
  casesVersion = ref("");
const deletionBlocked = computed(
  () => busy.value || props.parentBusy || props.pendingJobs > 0,
);
let caseRequest = 0;
const time = (value) =>
  value ? value.replace("T", " ").replace(/Z$/, " UTC") : "未登记";
async function loadCases() {
  const request = ++caseRequest;
  const inventory = await props.api("/knowledge/cases");
  if (request !== caseRequest) return;
  cases.value = inventory.items;
  casesVersion.value = inventory.knowledge_version;
}
watch(
  () => props.knowledge?.version,
  () => perform(loadCases),
  { immediate: true },
);
const form = ref({
  title: "",
  symptoms: "",
  cause: "",
  actions: "",
  outcome: "",
  evidence: "",
  reviewer: "",
});
async function perform(action) {
  busy.value = true;
  try {
    await action();
  } catch (e) {
    emit("error", e);
  } finally {
    busy.value = false;
  }
}
async function upload(event) {
  const file = event.target.files[0];
  if (!file) return;
  await perform(async () => {
    if (file.size > 8000000)
      throw new Error("证据包超过 8 MB；大文件请使用本地导入命令。");
    attachment.value = await props.api(
      `/events/${props.eventId}/evidence`,
      JSON.parse(await file.text()),
    );
    emit(
      "notice",
      `已加入 ${attachment.value.documents} 条观测，下次查询将使用新的快照。`,
    );
  });
  event.target.value = "";
}
async function saveCase() {
  await perform(async () => {
    const value = form.value,
      split = (s) =>
        s
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean);
    const references = split(value.evidence);
    await props.api("/knowledge/cases", {
      case: {
        schema_version: "1.0",
        case_id: "case-human-" + crypto.randomUUID().replaceAll("-", ""),
        source_incident_id: props.incident.incident_id,
        system: props.incident.system,
        title: value.title,
        symptoms: split(value.symptoms),
        rca_candidates: [],
        verified_root_cause: value.cause,
        actions: split(value.actions),
        evidence_refs: references,
        human_verified: agreed.value,
        verified_by: value.reviewer,
        verified_at: new Date().toISOString(),
        source: "human-reviewed operational record",
        raw_ref: references[0] || "",
      },
      outcome: value.outcome,
      outcome_evidence: value.evidence,
      attestation: agreed.value,
    });
    agreed.value = false;
    await loadCases();
    emit("refresh");
    emit("notice", "已保存人工审核记录并发布新的知识库版本。");
  });
}
async function deleteCase(item) {
  if (deletionBlocked.value) return;
  if (
    !window.confirm(
      `从当前知识库删除案例“${item.title}”？此操作不可撤回，将发布不含此案例的新版本。历史知识版本和既有查询快照会保留，以便审计与复现实验；其中仍可包含此案例。`,
    )
  )
    return;
  await perform(async () => {
    const deleted = await props.api(
      `/knowledge/cases/${encodeURIComponent(item.case_id)}`,
      undefined,
      "DELETE",
    );
    await loadCases();
    emit("refresh");
    emit(
      "notice",
      `已从当前知识库删除案例，释放 ${Number(deleted.reclaimed_bytes || 0).toLocaleString()} 字节。历史版本与查询快照保留。`,
    );
  });
}
</script>

<template>
  <section class="knowledge-page">
    <div class="intro">
      <span class="eyebrow">KNOWLEDGE / 知识资产</span>
      <h1>每次查询，都有可追溯的来源。</h1>
      <p>长期知识按版本保存；当前事件的观测在查询时汇入独立快照。</p>
    </div>
    <div class="inventory">
      <div>
        <strong>{{ (knowledge?.counts?.runbook || 0).toLocaleString() }}</strong
        ><span>运维手册</span>
      </div>
      <div>
        <strong>{{ knowledge?.counts?.case || 0 }}</strong
        ><span>已确认案例（人工登记）</span>
      </div>
      <div>
        <strong>{{ knowledge?.systems?.length || 0 }}</strong
        ><span>系统范围</span>
      </div>
    </div>
    <dl class="version">
      <dt>当前版本</dt>
      <dd>{{ knowledge?.version || "尚未登记长期知识库" }}</dd>
      <dt>适用系统</dt>
      <dd>{{ knowledge?.systems?.join(" · ") || "—" }}</dd>
    </dl>
    <section class="case-inventory">
      <div class="section-title">
        <h2>已确认案例（人工登记）</h2>
        <span>当前版本 · {{ cases.length }} 条</span>
      </div>
      <p class="hint">
        核验人和核验结果由登记者自报，系统未进行外部身份或事实核验。教学、测试与占位记录不能计为真实研究案例。
      </p>
      <p v-if="pendingJobs" class="hint">
        任务排队或运行时暂停删除；完成后可继续。
      </p>
      <p v-if="busy && !casesVersion" class="hint">正在读取当前案例…</p>
      <p v-else-if="!cases.length" class="small-empty">
        当前知识版本没有已登记案例。
      </p>
      <article v-for="item in cases" :key="item.case_id" class="case-card">
        <div class="case-heading">
          <div>
            <h3>{{ item.title }}</h3>
            <code>{{ item.case_id }}</code>
          </div>
          <button
            class="button danger-button"
            @click="deleteCase(item)"
            :disabled="deletionBlocked"
          >
            删除案例
          </button>
        </div>
        <dl class="case-meta">
          <dt>系统</dt>
          <dd>{{ item.system || "未登记" }}</dd>
          <dt>来源事件</dt>
          <dd>{{ item.source_incident_id || "未登记" }}</dd>
          <dt>核验人（自报）</dt>
          <dd>{{ item.verified_by || "未登记" }}</dd>
          <dt>核验时间</dt>
          <dd>{{ time(item.verified_at) }}</dd>
          <dt>身份说明</dt>
          <dd>{{ item.identity_assurance || "未提供外部身份核验依据" }}</dd>
        </dl>
        <div class="case-content">{{ item.content }}</div>
      </article>
    </section>
    <div class="research-sections">
      <section>
        <h2>事件证据</h2>
        <p>
          将日志、调用链、拓扑和带图注的图片加入当前事件。证据需包含原始资产指纹、观测窗口及事件标识。
        </p>
        <p class="hint">
          当前事件：{{ incident?.incident_id || "请先在诊断工作台选择事件" }}
        </p>
        <label class="button file-button"
          >导入观测证据 JSON<input
            type="file"
            accept=".json"
            :disabled="!eventId || busy"
            @change="upload"
        /></label>
        <p v-if="attachment" class="hint">
          已保存 {{ attachment.documents }} 条 · {{ attachment.batch_id }}
        </p>
      </section>
      <section>
        <h2>正式历史案例</h2>
        <p>
          填写实际核验结果。工单草稿与算法候选需要人工核实后才能在这里入库。
        </p>
        <p class="hint">
          审核时间使用当前时间。新审核案例不会成为旧事件发生时已经可用的证据。
        </p>
        <form @submit.prevent="saveCase" class="case-form">
          <label
            >案例标题<input v-model="form.title" required maxlength="200"
          /></label>
          <label
            >审核人<input
              v-model="form.reviewer"
              required
              maxlength="100"
              autocomplete="name"
          /></label>
          <label class="wide"
            >观测症状（每行一项）<textarea
              v-model="form.symptoms"
              required
              rows="2"
            />
          </label>
          <label class="wide"
            >人工确认的根因<textarea v-model="form.cause" required rows="2" />
          </label>
          <label class="wide"
            >已执行的处置（每行一项）<textarea
              v-model="form.actions"
              required
              rows="2"
            />
          </label>
          <label class="wide"
            >处置结果<textarea v-model="form.outcome" required rows="2" />
          </label>
          <label class="wide"
            >根因及处置结果的核验依据（记录编号 / 引用，每行一项）<textarea
              v-model="form.evidence"
              required
              rows="3"
            />
          </label>
          <label class="attest wide"
            ><input
              type="checkbox"
              v-model="agreed"
              required
            />我已人工核验以上内容，所填处置实际执行，依据可追溯。</label
          >
          <button
            class="button primary"
            :disabled="busy || !agreed || !incident || !knowledge?.version"
          >
            保存审核并发布案例
          </button>
        </form>
      </section>
    </div>
  </section>
</template>

<style scoped>
.knowledge-page {
  max-width: 1060px;
  margin: auto;
  padding: 20px 0 60px;
}
.eyebrow {
  font-size: 11px;
  letter-spacing: 0.12em;
  color: #63776d;
}
.intro h1 {
  font-size: 28px;
  font-weight: 550;
  letter-spacing: -0.04em;
  margin: 14px 0;
}
.intro p,
.research-sections p {
  color: #666f69;
  font-size: 13px;
  line-height: 1.8;
}
.inventory {
  display: flex;
  gap: 70px;
  padding: 30px 0;
  border-bottom: 1px solid #e3e7e4;
}
.inventory div {
  display: grid;
  gap: 6px;
}
.inventory strong {
  font-size: 32px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.inventory span,
.version dt {
  font-size: 12px;
  color: #6b756e;
}
.version {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 14px;
  font-size: 12px;
  padding: 18px 0;
}
.version dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.research-sections {
  display: grid;
  grid-template-columns: 1fr 1.6fr;
  gap: 48px;
  border-top: 1px solid #e3e7e4;
  padding-top: 24px;
}
.case-inventory {
  padding: 24px 0;
  border-top: 1px solid #e3e7e4;
}
.case-inventory h2 {
  font-size: 16px;
  font-weight: 550;
}
.case-card {
  padding: 22px 0;
  border-bottom: 1px solid #e3e7e4;
}
.case-card:last-child {
  border-bottom: 0;
}
.case-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.case-heading > div {
  min-width: 0;
}
.case-heading h3 {
  font-size: 14px;
  font-weight: 550;
  overflow-wrap: anywhere;
  margin-bottom: 5px;
}
.case-heading button {
  flex-shrink: 0;
}
.case-meta {
  grid-template-columns: 100px minmax(0, 1fr);
  font-size: 11px;
  gap: 7px 12px;
  margin: 15px 0;
}
.case-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.85;
  color: #555e58;
  padding: 15px;
  background: #f8f9f8;
  border-radius: 8px;
}
.research-sections h2 {
  font-size: 16px;
  font-weight: 550;
}
.case-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 22px;
}
.case-form label {
  display: grid;
  gap: 7px;
  font-size: 12px;
}
.case-form .wide {
  grid-column: 1/-1;
}
.case-form textarea,
.case-form input:not([type="checkbox"]) {
  width: 100%;
  padding: 9px;
  border: 1px solid #d8ded9;
  border-radius: 5px;
  font: inherit;
  line-height: 1.6;
  background: #fff;
}
.case-form .attest {
  display: flex;
  align-items: flex-start;
  line-height: 1.7;
}
.attest input {
  margin-top: 4px;
  flex-shrink: 0;
}
.case-form button {
  grid-column: 1/-1;
  justify-self: start;
}
.hint {
  overflow-wrap: anywhere;
}
@media (max-width: 800px) {
  .research-sections {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .inventory {
    gap: 35px;
  }
  .inventory div {
    min-width: 0;
  }
  .intro h1 {
    font-size: 23px;
  }
}
</style>
