<script setup>
import { computed, nextTick, onMounted, ref } from "vue";
import Icon from "./Icon.vue";
const props = defineProps({ initial: Object, api: Function });
const emit = defineEmits(["close", "saved"]);
const dialog = ref(null),
  tab = ref("general"),
  busy = ref(false),
  message = ref(""),
  problem = ref(null);
const fields = [
  "cloud_enabled",
  "generation_provider",
  "qwen_model",
  "glm_model",
  "qwen_region",
  "timeout_seconds",
  "proxy",
];
const form = ref(Object.fromEntries(fields.map((k) => [k, props.initial[k]])));
const credentials = ref(props.initial.credentials),
  baseline = ref(JSON.stringify(form.value));
const qwenKey = ref(""),
  glmKey = ref(""),
  clearQwen = ref(false),
  clearGlm = ref(false);
const dirty = computed(
  () =>
    JSON.stringify(form.value) !== baseline.value ||
    qwenKey.value ||
    glmKey.value ||
    clearQwen.value ||
    clearGlm.value,
);
const status = (provider) =>
  ({
    saved: "已加密保存",
    environment: "来自服务端环境",
    missing: "未配置",
    disabled: "已清除",
  })[credentials.value[provider]];
async function save() {
  busy.value = true;
  message.value = "";
  problem.value = null;
  try {
    const value = await props.api("/settings", {
      ...form.value,
      qwen_key: qwenKey.value || null,
      glm_key: glmKey.value || null,
      clear_qwen_key: clearQwen.value,
      clear_glm_key: clearGlm.value,
    });
    credentials.value = value.credentials;
    qwenKey.value = "";
    glmKey.value = "";
    clearQwen.value = false;
    clearGlm.value = false;
    baseline.value = JSON.stringify(form.value);
    message.value = "设置已保存";
    emit("saved", value);
  } catch (e) {
    problem.value = e.problem || { message: e.message };
  } finally {
    busy.value = false;
  }
}
async function probe(service) {
  busy.value = true;
  message.value = "";
  problem.value = null;
  try {
    await props.api("/settings/test", { service });
    message.value = `${service === "glm" ? "GLM" : "Qwen Embedding"} 连接正常`;
  } catch (e) {
    problem.value = e.problem || { message: e.message };
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  await nextTick();
  dialog.value.showModal();
});
</script>
<template>
  <dialog
    ref="dialog"
    class="settings-dialog"
    aria-labelledby="settings-title"
    @close="emit('close')"
    @click="
      (e) => {
        if (e.target === dialog) dialog.close();
      }
    "
  >
    <div class="settings-header">
      <h2 id="settings-title">设置</h2>
      <button class="icon-button" aria-label="关闭设置" @click="dialog.close()">
        <Icon name="close" />
      </button>
    </div>
    <div class="settings-layout">
      <nav class="settings-tabs" aria-label="设置分类">
        <button :class="{ active: tab === 'general' }" @click="tab = 'general'">
          <Icon name="sliders" />通用
        </button>
        <button :class="{ active: tab === 'models' }" @click="tab = 'models'">
          <Icon name="cloud" />模型服务
        </button>
        <button :class="{ active: tab === 'network' }" @click="tab = 'network'">
          <Icon name="network" />网络
        </button>
      </nav>
      <form class="settings-body" id="service-settings" @submit.prevent="save">
        <template v-if="tab === 'general'">
          <h3>通用</h3>
          <label class="setting-row"
            ><span
              >Cloud 模式<small
                >允许云端检索与生成，调用按服务商计费。</small
              ></span
            ><input class="switch" type="checkbox" v-model="form.cloud_enabled"
          /></label>
          <label class="setting-row"
            ><span>默认生成服务</span
            ><select v-model="form.generation_provider">
              <option value="qwen">Qwen</option>
              <option value="glm">GLM · 智谱</option>
            </select></label
          >
          <p class="hint">实验中的生成模式仍可单独选择。</p>
        </template>
        <template v-if="tab === 'models'">
          <h3>模型服务</h3>
          <section class="provider-block">
            <div class="provider-heading">
              <h4>GLM <span>生成</span></h4>
              <span class="credential-status">{{ status("glm") }}</span>
            </div>
            <label
              >GLM API Key<input
                type="password"
                v-model="glmKey"
                autocomplete="new-password"
                spellcheck="false"
                :disabled="clearGlm"
                placeholder="输入新密钥，留空保留现有配置"
            /></label>
            <label
              >GLM 模型<input
                v-model="form.glm_model"
                placeholder="glm-4.7"
                required
                maxlength="80"
            /></label>
            <div class="provider-actions">
              <label class="check-label"
                ><input
                  type="checkbox"
                  v-model="clearGlm"
                  @change="glmKey = ''"
                />清除已配置密钥</label
              ><button
                type="button"
                class="button"
                :disabled="busy || !!dirty || !form.cloud_enabled"
                @click="probe('glm')"
              >
                测试 GLM 连接
              </button>
            </div>
          </section>
          <section class="provider-block">
            <div class="provider-heading">
              <h4>Qwen <span>Embedding / 重排 / 生成</span></h4>
              <span class="credential-status">{{ status("qwen") }}</span>
            </div>
            <label
              >Qwen API Key<input
                type="password"
                v-model="qwenKey"
                autocomplete="new-password"
                spellcheck="false"
                :disabled="clearQwen"
                placeholder="输入新密钥，留空保留现有配置"
            /></label>
            <div class="settings-two-col">
              <label
                >地域<select v-model="form.qwen_region">
                  <option value="cn">中国内地</option>
                  <option value="intl">国际（新加坡）</option>
                  <option value="us">美国</option>
                </select></label
              ><label
                >Qwen 生成模型<select v-model="form.qwen_model">
                  <option>qwen-plus</option>
                  <option>qwen-flash</option>
                </select></label
              >
            </div>
            <p class="hint">
              Embedding：text-embedding-v4 · 重排：gte-rerank-v2
            </p>
            <div class="provider-actions">
              <label class="check-label"
                ><input
                  type="checkbox"
                  v-model="clearQwen"
                  @change="qwenKey = ''"
                />清除已配置密钥</label
              ><button
                type="button"
                class="button"
                :disabled="busy || !!dirty || !form.cloud_enabled"
                @click="probe('qwen')"
              >
                测试 Qwen 连接
              </button>
            </div>
          </section>
          <p class="hint">
            密钥仅在本机加密保存。先保存再测试；连接测试会调用模型。
          </p>
        </template>
        <template v-if="tab === 'network'">
          <h3>网络</h3>
          <label class="setting-field"
            >本地代理<input
              v-model="form.proxy"
              placeholder="http://127.0.0.1:7897"
            /><small>留空直连。</small></label
          >
          <label class="setting-field"
            >请求超时（秒）<input
              type="number"
              v-model.number="form.timeout_seconds"
              min="10"
              max="120"
              required
          /></label>
        </template>
      </form>
    </div>
    <div v-if="problem" class="settings-feedback error" role="alert">
      <strong>{{ problem.message }}</strong>
      <p>{{ problem.hint }}</p>
      <p v-for="field in problem.details" :key="field">{{ field }}</p>
    </div>
    <div v-if="message" class="settings-feedback" role="status">
      <Icon name="check" :size="16" />{{ message }}
    </div>
    <footer class="settings-footer">
      <span>{{ dirty ? "有未保存的更改" : "已与本地服务同步" }}</span
      ><button
        class="button primary"
        type="submit"
        form="service-settings"
        :disabled="busy || !dirty"
      >
        {{ busy ? "处理中…" : "保存设置" }}
      </button>
    </footer>
  </dialog>
</template>
