<script setup>
import { ref } from "vue";
const props = defineProps(["api", "result"]);
const emit = defineEmits(["notice", "error"]);
const reviewer = ref(""),
  notes = ref(""),
  ratings = ref({}),
  busy = ref(false);
async function save() {
  busy.value = true;
  try {
    const relevance = Object.fromEntries(
      Object.entries(ratings.value)
        .filter(([, v]) => v !== "")
        .map(([k, v]) => [k, Number(v)]),
    );
    await props.api(
      `/orders/${props.result.record.generation_run_id}/annotations`,
      { reviewer: reviewer.value, notes: notes.value, relevance },
    );
    emit("notice", "已保存独立的检索相关性标注；未标注项保留未知。");
  } catch (e) {
    emit("error", e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <details class="relevance">
    <summary>人工标注检索相关性</summary>
    <p>
      0 不相关 · 1 有关联 · 2
      直接有助于验证或处置。未评项保留空白，陈述支持度在工单复核中记录。
    </p>
    <form @submit.prevent="save">
      <label>标注人<input v-model="reviewer" required maxlength="100" /></label>
      <label
        v-for="item in result.pack.pack.items"
        :key="item.evidence.evidence_id"
        ><span>{{ item.evidence.evidence_id }}</span
        ><select v-model="ratings[item.evidence.evidence_id]">
          <option value="">未评</option>
          <option :value="0">0 · 不相关</option>
          <option :value="1">1 · 有关联</option>
          <option :value="2">2 · 直接有用</option>
        </select></label
      >
      <label>标注理由<textarea v-model="notes" required rows="2" /></label
      ><button class="button" :disabled="busy">保存相关性标注</button>
    </form>
  </details>
</template>
<style scoped>
.relevance {
  margin-top: 24px;
  border-top: 1px solid #e2e6e3;
  padding-top: 18px;
  font-size: 12px;
}
.relevance summary {
  cursor: pointer;
  font-weight: 600;
}
.relevance p {
  line-height: 1.8;
  color: #667069;
}
.relevance form {
  display: grid;
  gap: 12px;
}
.relevance label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.relevance label span {
  overflow-wrap: anywhere;
}
.relevance input,
.relevance textarea,
.relevance select {
  max-width: 60%;
  padding: 7px;
  border: 1px solid #dce1dd;
  border-radius: 4px;
  font: inherit;
}
.relevance button {
  justify-self: start;
}
</style>
