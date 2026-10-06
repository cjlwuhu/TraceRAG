<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import Icon from "./Icon.vue";
import ServiceSettings from "./ServiceSettings.vue";
import KnowledgePanel from "./KnowledgePanel.vue";
import RelevanceReview from "./RelevanceReview.vue";
const knowledge = ref(null),
  knowledgeSystem = ref("zte-aiops2024"),
  corpusMode = ref("current");

const railCollapsed = ref(false),
  settingsOpen = ref(false),
  serviceSettings = ref(null);
const logs = ref([]),
  clientLogs = ref([]),
  logLevel = ref("all"),
  connectionLost = ref(false),
  activeProblem = ref(null);
const sectionTitle = computed(
  () =>
    ({
      workspace: "诊断工作台",
      orders: "工单与复核",
      experiments: "实验记录",
      logs: "运行日志",
      knowledge: "知识与案例",
    })[section.value],
);
const visibleLogs = computed(() =>
  [...clientLogs.value, ...logs.value]
    .filter((x) => logLevel.value === "all" || x.level === logLevel.value)
    .sort((a, b) => b.time.localeCompare(a.time)),
);
const logErrors = computed(
  () => logs.value.filter((x) => x.level === "error").length,
);
const statusLabels = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
};

const section = ref("workspace"),
  boot = ref(null),
  events = ref([]),
  eventId = ref(""),
  incident = ref(null);
const jobs = ref([]),
  orders = ref([]),
  result = ref(null),
  config = ref(null),
  gen = ref(null);
const query = ref("当前异常如何验证和处置"),
  error = ref(""),
  notice = ref(""),
  busy = ref(false);
const signal = ref(null),
  csvFile = ref(null),
  showUpload = ref(false),
  advanced = ref("");
const watchingJob = ref(sessionStorage.getItem("ops-job") || ""),
  selectedOrders = ref([]),
  comparisons = ref([]);
const draft = ref(null),
  ratings = ref({}),
  reviewer = ref(""),
  reviewNotes = ref(""),
  decision = ref("needs_revision");
const parentRevision = ref(null),
  savedRevision = ref(null),
  activeEvidence = ref("");
let timer,
  stopped = false,
  initialized = false;
const clone = (value) => JSON.parse(JSON.stringify(value));
const labels = {
  doc: "运维手册",
  case: "历史案例",
  metric: "时序指标",
  log: "日志",
  trace: "调用链",
  topology: "拓扑",
  image: "图片图注",
};
const currentJob = computed(() =>
  jobs.value.find((j) => j.id === watchingJob.value),
);
const pending = computed(() =>
  jobs.value.filter((j) => ["queued", "running"].includes(j.status)),
);
const evidence = computed(() => result.value?.pack?.pack.items || []);
const claims = computed(() => {
  if (!draft.value) return [];
  return [
    { title: "事件摘要", claim: draft.value.summary },
    ...draft.value.root_cause_candidates.map((x, i) => ({
      title: `候选 ${i + 1} · 未确认`,
      claim: x.statement,
      extra: x.verification,
    })),
    ...draft.value.verification_steps.map((x, i) => ({
      title: `验证 ${i + 1}`,
      claim: x,
    })),
    ...draft.value.proposed_actions.map((x, i) => ({
      title: `建议 ${i + 1} · 未执行`,
      claim: x,
      extra: `前提：${x.preconditions}；风险：${x.risks.join("；")}；回退：${x.rollback}`,
    })),
  ];
});
const countTypes = computed(() =>
  Object.fromEntries(
    Object.keys(labels).map((k) => [
      k,
      evidence.value.filter((x) => x.evidence.type === k).length,
    ]),
  ),
);
const short = (value) => (value ? value.slice(0, 16) + "…" : "—");
const assetUrl = (item) => {
  const event = events.value.find(
    (e) => e.incident_id === item.metadata.source_incident_id,
  );
  return event
    ? `/api/events/${event.id}/assets/${item.metadata.asset_sha256}`
    : null;
};
const time = (value) =>
  value ? value.replace("T", " ").slice(0, 19) + " UTC" : "—";

async function api(path, body) {
  let response;
  try {
    response = await fetch(
      "/api" + path,
      body === undefined
        ? {}
        : {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Console-Token": boot.value.token,
            },
            body: JSON.stringify(body),
          },
    );
  } catch {
    const e = new Error("无法连接本地服务");
    e.problem = {
      code: "connection",
      message: e.message,
      hint: "检查本地服务是否运行，恢复连接后将自动同步。",
    };
    throw e;
  }
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error(`服务响应异常（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const detail =
      typeof value.detail === "object" && !Array.isArray(value.detail)
        ? value.detail
        : {
            message:
              typeof value.detail === "string"
                ? value.detail
                : "请求未通过校验",
          };
    const e = new Error(detail.message);
    e.problem = detail;
    if (path !== "/logs") {
      try {
        logs.value = await api("/logs");
        e.serverLogged = true;
      } catch {
        /* Keep the original error. */
      }
    }
    throw e;
  }
  return value;
}
function report(e) {
  activeProblem.value = e.problem || { message: e.message };
  error.value = activeProblem.value.message;
  if (
    !e.serverLogged &&
    !clientLogs.value.some(
      (x) => x.message === e.message && Date.now() - Date.parse(x.time) < 30000,
    )
  )
    clientLogs.value = [
      {
        id: "client-" + Date.now(),
        time: new Date().toISOString(),
        level: "error",
        message: e.message,
        phase: "浏览器",
        problem: activeProblem.value,
      },
      ...clientLogs.value,
    ].slice(0, 50);
}
async function openSettings() {
  await safely(async () => {
    serviceSettings.value = await api("/settings");
    settingsOpen.value = true;
  });
}
function settingsSaved(value) {
  serviceSettings.value = value;
  boot.value.cloud_enabled = value.cloud_enabled;
  const model =
    value.generation_provider === "glm" ? value.glm_model : value.qwen_model;
  gen.value.model = model;
  boot.value.generation.model = model;
  syncAdvanced();
}
async function safely(action) {
  error.value = "";
  notice.value = "";
  busy.value = true;
  try {
    await action();
  } catch (e) {
    report(e);
  } finally {
    busy.value = false;
  }
}
async function refresh() {
  const [e, j, o, l, k] = await Promise.all([
    api("/events"),
    api("/jobs"),
    api("/orders"),
    api("/logs"),
    api("/knowledge"),
  ]);
  events.value = e.items;
  jobs.value = j;
  orders.value = o;
  logs.value = l;
  knowledge.value = k;
}
async function chooseEvent() {
  incident.value = eventId.value ? await api("/events/" + eventId.value) : null;
  result.value = null;
  draft.value = null;
}
function syncAdvanced() {
  advanced.value = JSON.stringify(
    { retrieval: config.value, generation: gen.value },
    null,
    2,
  );
}
function preset(name) {
  config.value = clone(boot.value.retrieval);
  gen.value = clone(boot.value.generation);
  if (name === "no_rca") {
    config.value.query.use_rca = false;
    gen.value.include_rca_candidates = false;
  }
  if (name === "doc_only") {
    Object.keys(labels).forEach((k) => {
      config.value.sources[k] = k === "doc";
    });
  }
  if (name === "multisource") {
    Object.keys(labels).forEach((k) => {
      config.value.sources[k] = true;
    });
    config.value.pack.top_k = 20;
    config.value.pack.max_context_chars = 40000;
  }
  if (name === "no_path") config.value.retrieval.path_enabled = false;
  if (name === "hybrid") {
    config.value.retrieval.mode = "hybrid";
    config.value.embedding.enabled = true;
  }
  syncAdvanced();
}
function modeChanged() {
  config.value.embedding.enabled = config.value.retrieval.mode !== "bm25";
  syncAdvanced();
}
async function applyAdvanced() {
  await safely(async () => {
    const value = await api("/validate-config", JSON.parse(advanced.value));
    config.value = value.retrieval;
    gen.value = value.generation;
    notice.value = "配置已通过后端校验并应用。";
  });
}
async function submitRun() {
  if (
    busy.value ||
    (!eventId.value && !knowledgeSystem.value) ||
    watchingJob.value ||
    !query.value.trim()
  )
    return;
  await safely(async () => {
    const job = await api("/runs", {
      event_id: eventId.value || null,
      knowledge_system: eventId.value ? null : knowledgeSystem.value,
      corpus_mode: eventId.value ? corpusMode.value : "current",
      query: query.value,
      retrieval: config.value,
      generation: gen.value,
    });
    watchingJob.value = job.id;
    sessionStorage.setItem("ops-job", job.id);
    result.value = null;
    draft.value = null;
    await refresh();
  });
}
async function loadOrder(id) {
  result.value = await api("/orders/" + id);
  draft.value =
    result.value.compatible && result.value.record.draft
      ? clone(result.value.record.draft)
      : null;
  ratings.value = {};
  reviewNotes.value = "";
  decision.value = "needs_revision";
  parentRevision.value = null;
  savedRevision.value = null;
  activeEvidence.value = "";
}
async function poll() {
  try {
    if (!initialized) {
      await initialize();
    }
    jobs.value = await api("/jobs");
    logs.value = await api("/logs");
    if (connectionLost.value) {
      boot.value = await api("/bootstrap");
      connectionLost.value = false;
      if (activeProblem.value?.code === "connection") error.value = "";
      clientLogs.value.unshift({
        id: "restored-" + Date.now(),
        time: new Date().toISOString(),
        level: "info",
        message: "连接已恢复",
        phase: "浏览器",
      });
    }
    const job = currentJob.value;
    if (job && !["queued", "running"].includes(job.status)) {
      if (job.status === "completed") {
        await refresh();
        if (job.result?.generation_run_id)
          await loadOrder(job.result.generation_run_id);
        else if (job.result?.event_id) {
          eventId.value = job.result.event_id;
          await chooseEvent();
          notice.value = "时序处理完成，事件已导入。";
          const merged = job.result.csv_normalization?.dropped_column_indices_zero_based?.length || 0;
          if (merged)
            notice.value += ` 已合并 ${merged} 列一致的 time，保留 ${job.result.csv_normalization.normalized_columns} 列；原始 CSV 和处理记录已保留。`;
          showUpload.value = false;
        } else
          notice.value =
            "本次未形成可供 RAG 使用的完整事件，请查看运行记录中的检测结果。";
      } else
        report({
          message: job.error || job.phase,
          problem: job.problem,
          serverLogged: true,
        });
      watchingJob.value = "";
      sessionStorage.removeItem("ops-job");
    }
  } catch (e) {
    if (!connectionLost.value) report(e);
    connectionLost.value = true;
  } finally {
    if (!stopped) timer = setTimeout(poll, pending.value.length ? 1800 : 6000);
  }
}
async function importBundle(event) {
  const file = event.target.files[0];
  if (!file) return;
  await safely(async () => {
    if (file.size > 8_000_000) throw new Error("文件超过 8 MB 上限");
    const imported = await api("/events", JSON.parse(await file.text()));
    await refresh();
    eventId.value = imported.event_id;
    await chooseEvent();
    notice.value = "事件已导入。";
  });
  event.target.value = "";
}
async function processCsv() {
  await safely(async () => {
    if (!csvFile.value || csvFile.value.size > 6_000_000)
      throw new Error("请选择不超过 6 MB 的 CSV");
    const options = clone(signal.value);
    if (options.detection_enabled) options.trigger_timestamp = null;
    const job = await api("/telemetry", {
      csv: await csvFile.value.text(),
      options,
    });
    watchingJob.value = job.id;
    sessionStorage.setItem("ops-job", job.id);
    await refresh();
    notice.value = "已提交时序处理。";
  });
}
function claimEdited(i) {
  ratings.value[i] = "uncertain";
  savedRevision.value = null;
}
async function cite(id) {
  activeEvidence.value = id;
  await nextTick();
  document
    .getElementById(id)
    ?.scrollIntoView({ behavior: "smooth", block: "center" });
}
async function saveReview() {
  await safely(async () => {
    savedRevision.value = await api(
      `/orders/${result.value.record.generation_run_id}/reviews`,
      {
        draft: draft.value,
        reviewer: reviewer.value,
        decision: decision.value,
        notes: reviewNotes.value,
        claims: claims.value.map((_, i) => ({
          claim_index: i,
          support: ratings.value[i] || "uncertain",
          note: "",
        })),
        parent_revision_id: parentRevision.value,
      },
    );
    parentRevision.value = savedRevision.value.revision_id;
    result.value.revisions.push(savedRevision.value);
    notice.value = "已保存新的人工修订版本。";
  });
}
function openRevision(revision) {
  draft.value = clone(revision.draft);
  reviewer.value = revision.reviewer;
  reviewNotes.value = revision.notes;
  decision.value = revision.decision;
  ratings.value = Object.fromEntries(
    revision.claims.map((c) => [c.claim_index, c.support]),
  );
  parentRevision.value = revision.revision_id;
  savedRevision.value = revision;
}
async function compare() {
  await safely(async () => {
    comparisons.value = await Promise.all(
      selectedOrders.value.map((id) => api("/orders/" + id)),
    );
  });
}
async function initialize() {
  boot.value = await api("/bootstrap");
  config.value = clone(boot.value.retrieval);
  gen.value = clone(boot.value.generation);
  signal.value = clone(boot.value.signal_options);
  syncAdvanced();
  await refresh();
  eventId.value =
    events.value.find(
      (e) => e.methods.length && e.detection.source === "anomaly_detector",
    )?.id ||
    events.value[0]?.id ||
    "";
  await chooseEvent();
  initialized = true;
}
onMounted(async () => {
  await safely(initialize);
  poll();
});
onUnmounted(() => {
  stopped = true;
  clearTimeout(timer);
});
</script>

<template>
  <div class="app-shell" :class="{ 'rail-collapsed': railCollapsed }">
    <aside class="rail">
      <div class="brand-row">
        <a class="brand" href="#" @click.prevent="section = 'workspace'"
          >循证<span>TraceRAG</span></a
        ><button
          class="icon-button"
          aria-label="收起侧栏"
          @click="railCollapsed = true"
        >
          <Icon name="panel" />
        </button>
      </div>
      <nav aria-label="主导航">
        <button
          :class="{ selected: section === 'workspace' }"
          @click="section = 'workspace'"
        >
          <Icon name="workspace" />诊断工作台
        </button>
        <button
          :class="{ selected: section === 'orders' }"
          @click="section = 'orders'"
        >
          <Icon name="file" />工单与复核<span class="nav-count">{{
            orders.length
          }}</span>
        </button>
        <button
          :class="{ selected: section === 'experiments' }"
          @click="section = 'experiments'"
        >
          <Icon name="history" />实验记录
        </button>
        <button
          :class="{ selected: section === 'knowledge' }"
          @click="
            safely(async () => {
              await refresh();
              section = 'knowledge';
            })
          "
        >
          <Icon name="file" />知识与案例
        </button>
      </nav>
      <div class="recent-orders">
        <div class="rail-label">最近工单</div>
        <button
          v-for="order in orders.slice(0, 7)"
          :key="order.id"
          @click="
            safely(async () => {
              await loadOrder(order.id);
              section = 'orders';
            })
          "
        >
          <span>{{ order.incident_id }}</span
          ><small
            >{{ time(order.created_at_utc).slice(5, 16) }} ·
            {{ order.mode === "cloud" ? "Cloud" : "离线" }}</small
          >
        </button>
        <p v-if="!orders.length" class="hint">暂无工单</p>
      </div>
      <div class="rail-bottom">
        <button
          :class="{ selected: section === 'logs' }"
          @click="section = 'logs'"
        >
          <Icon name="log" />运行日志<span v-if="logErrors" class="nav-count">{{
            logErrors
          }}</span></button
        ><button @click="openSettings" :disabled="!boot">
          <Icon name="settings" />设置
        </button>
        <div class="local-profile">
          <span class="avatar">研</span
          ><span>本地工作区<small>TraceRAG · RCA</small></span>
        </div>
      </div>
    </aside>
    <main>
      <header class="topbar">
        <div>
          <button
            v-if="railCollapsed"
            class="icon-button"
            aria-label="展开侧栏"
            @click="railCollapsed = false"
          >
            <Icon name="panel" /></button
          ><span>{{ sectionTitle }}</span>
        </div>
        <button class="mode-pill" @click="openSettings" :disabled="!boot">
          <span
            class="status-dot"
            :class="{ disconnected: connectionLost }"
          ></span
          >{{
            connectionLost ? "未连接" : boot?.cloud_enabled ? "Cloud" : "离线"
          }}<Icon name="chevron" :size="14" />
        </button>
      </header>
      <div v-if="error" class="message error" role="alert">
        <div>
          <strong>{{ error }}</strong
          ><span v-if="activeProblem?.hint">{{ activeProblem.hint }}</span
          ><span v-if="activeProblem?.http_status"
            >HTTP {{ activeProblem.http_status }} ·
            {{ activeProblem.service }}</span
          ><button class="text-button" @click="section = 'logs'">
            查看日志
          </button>
        </div>
        <button class="icon-button" aria-label="关闭错误" @click="error = ''">
          <Icon name="close" :size="16" />
        </button>
      </div>
      <div v-if="notice" class="message success" role="status">
        {{ notice
        }}<button aria-label="关闭提示" @click="notice = ''">×</button>
      </div>

      <div class="content" v-if="boot">
        <KnowledgePanel
          v-if="section === 'knowledge'"
          :api="api"
          :knowledge="knowledge"
          :event-id="eventId"
          :incident="incident"
          @refresh="safely(refresh)"
          @notice="notice = $event"
          @error="report"
        />
        <template v-if="section === 'workspace'">
          <section class="event-bar">
            <div class="event-select">
              <label for="event">事件</label
              ><select
                id="event"
                v-model="eventId"
                @change="safely(chooseEvent)"
              >
                <option value="">运维文档问答（无事件）</option>
                <option v-for="e in events" :key="e.id" :value="e.id">
                  {{ e.system }} · {{ e.incident_id }} · {{ e.id.slice(-6) }}
                </option>
              </select>
            </div>
            <label class="button file-button"
              >导入信号包<input
                type="file"
                accept=".json,application/json"
                @change="importBundle"
                :disabled="busy" /></label
            ><button
              class="button"
              @click="showUpload = !showUpload"
              :aria-expanded="showUpload"
            >
              {{ showUpload ? "收起 CSV" : "上传 CSV" }}
            </button>
          </section>
          <div class="corpus-choice">
            <label v-if="!eventId"
              >文档范围
              <select aria-label="文档范围" v-model="knowledgeSystem">
                <option
                  v-for="s in knowledge?.systems || []"
                  :key="s"
                  :value="s"
                >
                  {{ s }}
                </option>
              </select></label
            >
            <label v-else
              >知识来源
              <select aria-label="知识来源" v-model="corpusMode">
                <option value="current">当前知识库 + 本事件观测</option>
                <option value="event_snapshot">导入时快照（复现实验）</option>
              </select></label
            >
            <span
              >知识版本 {{ short(knowledge?.version) }} ·
              {{ knowledge?.counts?.runbook || 0 }} 篇手册</span
            >
          </div>
          <section v-if="showUpload" class="upload-panel panel">
            <div class="section-title">
              <h2>导入时序</h2>
              <span>CSV · 最大 6 MB</span>
            </div>
            <p>time 列为 Unix 秒，其他列为数值指标。逐行一致的重复 time 列会合并并保留处理记录；重复指标或冲突时间列会报错。</p>
            <div class="upload-grid">
              <label
                >时序 CSV<input
                  type="file"
                  accept=".csv,text/csv"
                  @change="csvFile = $event.target.files[0]" /></label
              ><label>系统名称<input v-model="signal.system" /></label
              ><label
                >缺失值处理<select v-model="signal.preprocessing">
                  <option value="strict">严格校验</option>
                  <option value="causal_ffill5_zero_v1">
                    历史前向填充（保留处理记录）
                  </option>
                </select></label
              ><label class="toggle"
                ><input
                  type="checkbox"
                  v-model="signal.detection_enabled"
                />异常检测</label
              ><label
                >MAD 阈值<input
                  type="number"
                  v-model.number="signal.threshold"
                  min="0.1"
                  step="0.1" /></label
              ><label v-if="!signal.detection_enabled"
                >人工事件时间（Unix 秒）<input
                  type="number"
                  v-model.number="signal.trigger_timestamp" /></label
              ><label v-if="signal.detection_enabled"
                >预热点数<input type="number" v-model.number="signal.warmup_points"
                  min="20" max="100000" step="1" /></label
              ><label v-if="signal.detection_enabled"
                >连续确认点数<input type="number" v-model.number="signal.consecutive_points"
                  min="1" max="10000" step="1" /></label
              ><label v-if="signal.detection_enabled"
                >最少异常指标数<input type="number" v-model.number="signal.minimum_metrics"
                  min="1" max="100000" step="1" /></label
              ><label v-if="signal.detection_enabled"
                >采样间隔上限（秒）<input type="number" v-model.number="signal.max_gap_seconds"
                  min="1" max="86400" step="1" /></label
              ><label class="toggle"
                ><input
                  type="checkbox"
                  v-model="signal.rca_enabled"
                />RCA</label
              ><label class="toggle"
                ><input
                  type="checkbox"
                  v-model="signal.baro"
                  :disabled="!signal.rca_enabled"
                />BARO</label
              ><label class="toggle"
                ><input
                  type="checkbox"
                  v-model="signal.epsilon"
                  :disabled="!signal.rca_enabled"
                />ε-Diagnosis</label
              ><label class="toggle"
                ><input
                  type="checkbox"
                  v-model="signal.summaries_enabled"
                />指标摘要</label
              >
            </div>
            <button
              class="button primary"
              @click="processCsv"
              :disabled="busy || !csvFile || !boot.telemetry_enabled"
            >
              处理并导入
            </button>
          </section>
          <details v-if="incident" class="incident-details">
            <summary>
              {{ incident.system
              }}<span>{{ time(incident.detection.timestamp_utc) }}</span>
            </summary>
            <div class="incident-strip">
              <span>事件 {{ incident.incident_id }}</span
              ><span>来源 {{ incident.detection.source }}</span
              ><span
                >证据截止
                {{ time(incident.detection.evidence_cutoff_utc) }}</span
              >
            </div>
          </details>
          <div class="workspace-grid" :class="{ 'has-result': !!result }">
            <section class="workspace-controls">
              <details class="configuration">
                <summary>
                  <Icon name="sliders" :size="17" /><span>实验配置</span
                  ><span class="config-summary"
                    >{{ config.retrieval.mode.toUpperCase() }} ·
                    {{
                      gen.enabled
                        ? gen.mode === "cloud"
                          ? gen.model
                          : "离线摘录"
                        : "仅检索"
                    }}</span
                  ><Icon name="chevron" :size="16" />
                </summary>
                <div class="config-grid">
                  <label class="field-title" for="preset">消融预设</label
                  ><select id="preset" @change="preset($event.target.value)">
                    <option value="full">离线基线</option>
                    <option value="multisource">多源证据（最多 20 条）</option>
                    <option value="no_rca">检索与生成不使用 RCA 候选</option>
                    <option value="doc_only">仅手册证据</option>
                    <option value="no_path">关闭路径检索</option>
                    <option value="hybrid" :disabled="!boot.cloud_enabled">
                      Hybrid
                    </option>
                  </select>

                  <div class="control-group">
                    <h3>证据来源</h3>
                    <div class="checks">
                      <label v-for="(name, key) in labels" :key="key"
                        ><input
                          type="checkbox"
                          v-model="config.sources[key]"
                        />{{ name }}</label
                      >
                    </div>
                  </div>
                  <div class="control-group">
                    <h3>检索路径</h3>
                    <label class="toggle"
                      ><span>RCA 候选扩展查询</span
                      ><input
                        type="checkbox"
                        v-model="config.query.use_rca" /></label
                    ><label class="toggle"
                      ><span>文档路径检索</span
                      ><input
                        type="checkbox"
                        v-model="config.retrieval.path_enabled" /></label
                    ><label class="toggle"
                      ><span>RRF 排名融合</span
                      ><input
                        type="checkbox"
                        v-model="config.fusion.enabled" /></label
                    ><label class="field-title" for="retrieval">检索模式</label
                    ><select
                      id="retrieval"
                      v-model="config.retrieval.mode"
                      @change="modeChanged"
                    >
                      <option value="bm25">BM25</option>
                      <option value="dense" :disabled="!boot.cloud_enabled">
                        Dense
                      </option>
                      <option value="hybrid" :disabled="!boot.cloud_enabled">
                        Hybrid
                      </option></select
                    ><label class="toggle"
                      ><span>云端重排</span
                      ><input
                        type="checkbox"
                        v-model="config.reranker.enabled"
                        :disabled="!boot.cloud_enabled" /></label
                    ><label class="inline-field"
                      >最终证据条数<input
                        type="number"
                        v-model.number="config.pack.top_k"
                        min="1"
                        max="100"
                    /></label>
                  </div>
                  <div class="control-group">
                    <h3>工单生成</h3>
                    <label class="toggle"
                      ><span>生成草稿</span
                      ><input type="checkbox" v-model="gen.enabled" /></label
                    ><label class="toggle"
                      ><span>生成时提供 RCA 候选</span
                      ><input
                        type="checkbox"
                        v-model="gen.include_rca_candidates" /></label
                    ><label for="generation" class="sr-only">生成模式</label
                    ><select id="generation" v-model="gen.mode">
                      <option value="extractive">离线证据摘录</option>
                      <option value="cloud" :disabled="!boot.cloud_enabled">
                        {{ gen.model }} · Cloud
                      </option>
                    </select>
                  </div>
                  <details
                    class="advanced"
                    @toggle="$event.target.open && syncAdvanced()"
                  >
                    <summary>高级配置 JSON</summary>

                    <textarea
                      aria-label="完整实验配置"
                      v-model="advanced"
                      rows="13"
                      spellcheck="false"
                    ></textarea
                    ><button class="button" @click="applyAdvanced">
                      应用 JSON
                    </button>
                  </details>
                </div>
              </details>
              <div class="composer">
                <label for="question" class="sr-only">诊断问题</label
                ><textarea
                  id="question"
                  v-model="query"
                  rows="2"
                  maxlength="10000"
                  placeholder="输入诊断问题…"
                  @keydown.ctrl.enter.prevent="submitRun"
                  @keydown.meta.enter.prevent="submitRun"
                ></textarea>
                <div class="composer-actions">
                  <span>{{ gen.enabled ? "检索与工单" : "仅检索" }}</span
                  ><button
                    class="send-button"
                    aria-label="运行检索与工单"
                    title="运行 · Ctrl + Enter"
                    @click="submitRun"
                    :disabled="
                      busy ||
                      (!eventId && !knowledgeSystem) ||
                      !!watchingJob ||
                      !query.trim()
                    "
                  >
                    <Icon v-if="!watchingJob" name="arrow" /><span
                      v-else
                      class="spinner"
                    ></span>
                  </button>
                </div>
              </div>
            </section>
            <section class="results-area">
              <div v-if="currentJob" class="job-progress" role="status">
                <span class="spinner"></span>
                <div>
                  {{ currentJob.phase
                  }}<small
                    >{{ short(currentJob.id) }} · {{ currentJob.status }}</small
                  >
                </div>
              </div>
              <section v-if="!result" class="empty-result">
                <Icon name="workspace" :size="36" />
                <h1>开始一次诊断</h1>
                <p>
                  {{
                    events.length
                      ? "从事件中检索证据，生成工单。"
                      : "导入信号包或 CSV，开始分析。"
                  }}
                </p>
              </section>
              <template v-else
                ><div class="result-heading">
                  <h2>本次结果</h2>
                  <span
                    >{{ result.record.config.mode }} ·
                    {{ short(result.record.source_pack_id) }}</span
                  >
                </div>
                <div class="result-stats">
                  <span
                    ><b>{{ evidence.length }}</b> 条证据</span
                  ><span
                    ><b>{{ Object.keys(result.pack.routes).length }}</b>
                    条路由</span
                  ><span>{{ result.pack.pack.context_chars }} 字符</span
                  ><span class="tag">待复核</span>
                </div>
                <details class="result-notes">
                  <summary>证据边界</summary>
                  <p>
                    引用可定位不等于语义支持。<span v-if="countTypes.case"
                      >当前语料包含教学模拟案例。</span
                    >
                  </p>
                </details>
                <section class="panel">
                  <div class="section-title">
                    <h2>检索证据</h2>
                    <button class="text-button" @click="section = 'orders'">
                      审核工单 →
                    </button>
                  </div>
                  <article
                    v-for="item in evidence"
                    :key="item.evidence.evidence_id"
                    class="evidence-card"
                  >
                    <div class="evidence-meta">
                      <span class="tag" :class="item.evidence.type">{{
                        labels[item.evidence.type]
                      }}</span
                      ><span
                        >#{{ String(item.pack_rank).padStart(2, "0") }}</span
                      >
                    </div>
                    <h3>
                      {{
                        item.evidence.metadata.metric || item.evidence.source
                      }}
                    </h3>
                    <p>
                      {{ item.evidence.content.slice(0, 120)
                      }}{{ item.evidence.content.length > 120 ? "…" : "" }}
                    </p>
                    <details>
                      <summary>原文与路由分数</summary>
                      <pre>{{ item.evidence.content }}</pre>
                      <pre>{{ item.contributions }}</pre>
                      <p>
                        RRF {{ item.fusion_score ?? "未启用" }} · 重排
                        {{ item.rerank_score ?? "未启用" }}
                      </p>
                    </details>
                  </article>
                  <div v-if="!evidence.length" class="small-empty">
                    本次配置未检索到符合条件的证据，未生成诊断正文。
                  </div>
                  <details>
                    <summary>查询与配置</summary>
                    <pre>{{ result.pack.query_context }}</pre>
                    <pre>{{ result.pack.experiment.config }}</pre>
                  </details>
                </section></template
              >
            </section>
          </div>
        </template>

        <template v-if="section === 'orders'">
          <section class="event-bar">
            <div class="event-select">
              <label for="order"
                >历史工单 <span>最近 {{ orders.length }} 份 · UTC</span></label
              ><select
                id="order"
                :value="result?.record.generation_run_id || ''"
                @change="safely(() => loadOrder($event.target.value))"
              >
                <option value="" disabled>选择一份工单</option>
                <option
                  v-for="order in orders"
                  :key="order.id"
                  :value="order.id"
                >
                  {{ time(order.created_at_utc) }} · {{ order.mode }} ·
                  {{ order.compatible ? order.status : "旧结构归档" }} ·
                  {{ order.id.slice(-6) }}
                </option>
              </select>
            </div>
            <template v-if="result"
              ><a
                class="button"
                :href="`/api/orders/${result.record.generation_run_id}/download/md`"
                download
                >原稿 Markdown ↓</a
              ><a
                class="button"
                :href="`/api/orders/${result.record.generation_run_id}/download/json`"
                download
                >原稿 JSON ↓</a
              ></template
            >
          </section>
          <div v-if="!result" class="panel empty-result">
            <div class="empty-glyph">[ ↗ ]</div>
            <h2>选择工单</h2>
            <p></p>
          </div>
          <template v-else
            ><div class="incident-strip">
              <span>{{ result.record.incident_id }}</span
              ><span>{{ short(result.record.source_pack_id) }}</span
              ><span>根因：未确认 / 处置：未执行</span>
            </div>

            <details v-if="result.engineering_review" class="engineering">
              <summary>工程复查记录</summary>
              <pre>{{ result.engineering_review }}</pre>
            </details>
            <div class="review-grid">
              <section class="panel review-draft">
                <div class="section-title">
                  <h2>草稿与人工修订</h2>
                </div>
                <template v-if="draft"
                  ><div v-for="(entry, i) in claims" :key="i" class="claim">
                    <div class="claim-heading">
                      <label :for="`claim-${i}`">{{ entry.title }}</label
                      ><select
                        :aria-label="`条目 ${i + 1} 支持程度`"
                        v-model="ratings[i]"
                      >
                        <option :value="undefined">不确定（默认）</option>
                        <option value="uncertain">不确定</option>
                        <option value="supported">证据支持</option>
                        <option value="unsupported">证据不支持</option>
                      </select>
                    </div>
                    <textarea
                      :id="`claim-${i}`"
                      v-model="entry.claim.text"
                      rows="3"
                      @input="claimEdited(i)"
                    ></textarea>
                    <p v-if="entry.extra" class="hint">{{ entry.extra }}</p>
                    <button
                      v-for="c in entry.claim.citations"
                      :key="c.evidence_id"
                      class="citation"
                      @click="cite(c.evidence_id)"
                    >
                      <span>{{ short(c.evidence_id) }} ↗</span>“{{ c.quote }}”
                    </button>
                  </div>
                  <div class="missing">
                    <h3>待补充信息</h3>
                    <p v-for="m in draft.missing_information" :key="m">
                      {{ m }}
                    </p>
                    <p>影响范围：未知。观测证据不支持自动推断业务损失。</p>
                  </div>
                  <form class="review-form" @submit.prevent="saveReview">
                    <h3>保存修订</h3>
                    <label
                      >复核人<input
                        v-model="reviewer"
                        required
                        maxlength="100"
                        placeholder="姓名或实验标识" /></label
                    ><label
                      >复核意见<textarea
                        v-model="reviewNotes"
                        required
                        maxlength="5000"
                        rows="3"
                        placeholder="修改原因或待核查事项"
                      ></textarea></label
                    ><label
                      >草稿复核状态<select v-model="decision">
                        <option value="needs_revision">仍需修订</option>
                        <option value="reviewed_draft">已复核草稿</option>
                      </select></label
                    ><button
                      class="button primary"
                      type="submit"
                      :disabled="busy"
                    >
                      保存人工修订 →
                    </button>
                  </form>
                  <div v-if="savedRevision" class="revision-download">
                    <span>已保存 {{ short(savedRevision.revision_id) }}</span
                    ><a
                      :href="`/api/orders/${result.record.generation_run_id}/reviews/${savedRevision.revision_id}/download/md`"
                      download
                      >修订稿 Markdown ↓</a
                    ><a
                      :href="`/api/orders/${result.record.generation_run_id}/reviews/${savedRevision.revision_id}/download/json`"
                      download
                      >复核 JSON ↓</a
                    >
                  </div>
                  <details v-if="result.revisions.length">
                    <summary>
                      已保存版本（{{ result.revisions.length }}）
                    </summary>
                    <button
                      v-for="r in result.revisions"
                      :key="r.revision_id"
                      class="revision-link"
                      @click="openRevision(r)"
                    >
                      {{ time(r.created_at_utc) }} · {{ r.reviewer }} ·
                      {{ r.decision }}
                    </button>
                  </details></template
                >
                <div v-else class="small-empty">
                  {{
                    result.compatible
                      ? "本次未生成正文：" + result.record.status
                      : "旧结构归档，不可编辑。请重新生成符合现行契约的草稿。"
                  }}
                </div>
              </section>
              <section class="panel evidence-review">
                <div class="section-title">
                  <h2>证据原文</h2>
                  <span>{{ evidence.length }} ITEMS</span>
                </div>
                <article
                  v-for="item in evidence"
                  :key="item.evidence.evidence_id"
                  :id="item.evidence.evidence_id"
                  class="evidence-card"
                  :class="{
                    highlighted: activeEvidence === item.evidence.evidence_id,
                  }"
                >
                  <div class="evidence-meta">
                    <span class="tag" :class="item.evidence.type">{{
                      labels[item.evidence.type]
                    }}</span
                    ><span>#{{ item.pack_rank }}</span>
                  </div>
                  <h3>
                    {{ item.evidence.metadata.metric || item.evidence.source }}
                  </h3>
                  <code>{{ item.evidence.evidence_id }}</code>
                  <pre>{{ item.evidence.content }}</pre>
                  <img
                    v-if="
                      item.evidence.type === 'image' && assetUrl(item.evidence)
                    "
                    :src="assetUrl(item.evidence)"
                    alt="证据原始图像，解释以图注和来源为准"
                    style="max-width: 100%; height: auto"
                  />
                  <details>
                    <summary>来源与元数据</summary>
                    <pre>{{ item.evidence.raw_ref }}</pre>
                    <pre>{{ item.evidence.metadata }}</pre>
                  </details>
                </article>
                <RelevanceReview
                  :key="result.record.generation_run_id"
                  :api="api"
                  :result="result"
                  @notice="notice = $event"
                  @error="report"
                />
              </section></div
          ></template>
        </template>

        <template v-if="section === 'experiments'">
          <section class="panel">
            <div class="section-title">
              <h2>运行记录</h2>
              <button class="text-button" @click="safely(refresh)">
                刷新 ↻
              </button>
            </div>

            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>提交时间 / UTC</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>阶段 / 结果</th>
                    <th>配置与审计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in jobs" :key="j.id">
                    <td>
                      {{ time(j.created_at_utc)
                      }}<small>{{ short(j.id) }}</small>
                    </td>
                    <td>
                      {{ j.kind === "rag" ? "检索 → 工单" : "CSV → RCA" }}
                    </td>
                    <td>
                      <span class="job-state" :class="j.status">{{
                        statusLabels[j.status] || j.status
                      }}</span>
                    </td>
                    <td>
                      {{ j.problem?.message || j.phase
                      }}<small v-if="j.problem">{{ j.problem.hint }}</small
                      ><small v-if="j.result?.csv_normalization?.dropped_column_indices_zero_based?.length">
                        时间列合并 {{ j.result.csv_normalization.dropped_column_indices_zero_based.length }} 列 ·
                        {{ j.result.csv_normalization.raw_rows }} 行 ·
                        {{ j.result.csv_normalization.original_columns }} → {{ j.result.csv_normalization.normalized_columns }} 列
                      </small
                      ><button
                        v-if="j.result?.generation_run_id"
                        class="text-button"
                        @click="
                          safely(async () => {
                            await loadOrder(j.result.generation_run_id);
                            section = 'orders';
                          })
                        "
                      >
                        打开工单 ↗
                      </button>
                    </td>
                    <td>
                      <details>
                        <summary>记录</summary>
                        <pre>{{ j }}</pre>
                      </details>
                    </td>
                  </tr>
                </tbody>
              </table>
              <p v-if="!jobs.length" class="small-empty">
                还没有通过网页提交的实验。已有 CLI 工单可以在下方比较。
              </p>
            </div>
          </section>
          <section class="panel comparison">
            <div class="section-title">
              <h2>工单实验对照</h2>
              <span>选择 2–4 份</span>
            </div>

            <div class="compare-select">
              <label v-for="o in orders.filter((x) => x.compatible)" :key="o.id"
                ><input
                  type="checkbox"
                  :value="o.id"
                  v-model="selectedOrders"
                  :disabled="
                    selectedOrders.length >= 4 && !selectedOrders.includes(o.id)
                  "
                />{{ o.mode }} · {{ o.id.slice(-8) }} ·
                {{ time(o.created_at_utc) }}</label
              >
            </div>
            <button
              class="button primary"
              :disabled="busy || selectedOrders.length < 2"
              @click="compare"
            >
              生成对照表 →
            </button>
            <div v-if="comparisons.length" class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>比较项</th>
                    <th
                      v-for="c in comparisons"
                      :key="c.record.generation_run_id"
                    >
                      {{ c.record.generation_run_id.slice(-8) }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th>事件</th>
                    <td v-for="c in comparisons">{{ c.record.incident_id }}</td>
                  </tr>
                  <tr>
                    <th>原始问题</th>
                    <td v-for="c in comparisons">
                      {{ c.pack.query_context.question }}
                    </td>
                  </tr>
                  <tr>
                    <th>检索模式</th>
                    <td v-for="c in comparisons">
                      {{ c.pack.experiment.config.retrieval.mode }}
                    </td>
                  </tr>
                  <tr>
                    <th>查询 RCA / 路径 / 融合</th>
                    <td v-for="c in comparisons">
                      {{ c.pack.experiment.config.query.use_rca }} /
                      {{ c.pack.experiment.config.retrieval.path_enabled }} /
                      {{ c.pack.experiment.config.fusion.enabled }}
                    </td>
                  </tr>
                  <tr>
                    <th>证据数 / 证据包</th>
                    <td v-for="c in comparisons">
                      {{ c.record.evidence.length }} /
                      {{ c.record.source_pack_id }}
                    </td>
                  </tr>
                  <tr>
                    <th>生成模式 / 提供 RCA</th>
                    <td v-for="c in comparisons">
                      {{ c.record.config.mode }} /
                      {{ c.record.config.include_rca_candidates }}
                    </td>
                  </tr>
                  <tr>
                    <th>生成耗时（非性能基准）</th>
                    <td v-for="c in comparisons">
                      {{ c.record.elapsed_seconds.toFixed(3) }} s
                    </td>
                  </tr>
                  <tr>
                    <th>语义支持</th>
                    <td v-for="c in comparisons">尚未统一人工评测</td>
                  </tr>
                  <tr>
                    <th>完整配置</th>
                    <td v-for="c in comparisons">
                      <details>
                        <summary>展开</summary>
                        <pre>{{ c.pack.experiment.config }}</pre>
                        <pre>{{ c.record.config }}</pre>
                      </details>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>
        <template v-if="section === 'logs'">
          <section class="logs-view">
            <div class="section-title">
              <h1>运行日志</h1>
              <div class="log-tools">
                <select aria-label="日志级别" v-model="logLevel">
                  <option value="all">全部级别</option>
                  <option value="error">错误</option>
                  <option value="warning">警告</option>
                  <option value="info">信息</option></select
                ><button class="button" @click="safely(refresh)">刷新</button>
              </div>
            </div>
            <p v-if="!visibleLogs.length" class="small-empty">
              暂无{{ logLevel === "error" ? "错误" : "" }}日志
            </p>
            <article
              v-for="entry in visibleLogs"
              :key="entry.id"
              class="log-entry"
              :class="entry.level"
            >
              <div class="log-meta">
                <span class="tag">{{
                  { error: "错误", warning: "警告", info: "信息" }[entry.level]
                }}</span
                ><time>{{ time(entry.time) }}</time
                ><span>{{ entry.phase }}</span>
              </div>
              <h3>{{ entry.message }}</h3>
              <p v-if="entry.problem?.hint">{{ entry.problem.hint }}</p>
              <details v-if="entry.problem || entry.job_id">
                <summary>
                  详情<span v-if="entry.problem?.http_status">
                    · HTTP {{ entry.problem.http_status }}</span
                  >
                </summary>
                <dl>
                  <template v-if="entry.job_id"
                    ><dt>任务</dt>
                    <dd>{{ entry.job_id }}</dd></template
                  ><template v-if="entry.problem?.code"
                    ><dt>错误码</dt>
                    <dd>{{ entry.problem.code }}</dd></template
                  ><template v-if="entry.problem?.service"
                    ><dt>服务</dt>
                    <dd>{{ entry.problem.service }}</dd></template
                  ><template v-if="entry.problem?.phase"
                    ><dt>失败阶段</dt>
                    <dd>{{ entry.problem.phase }}</dd></template
                  >
                </dl>
                <p v-for="detail in entry.problem?.details || []" :key="detail">
                  {{ detail }}
                </p>
              </details>
            </article>
          </section>
        </template>
      </div>
      <div v-else class="loading" role="status">正在连接本地服务…</div>
    </main>
    <ServiceSettings
      v-if="settingsOpen"
      :initial="serviceSettings"
      :api="api"
      @saved="settingsSaved"
      @close="settingsOpen = false"
    />
  </div>
</template>
