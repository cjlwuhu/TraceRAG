import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";

// Each flow owns its imports/orders. Fixed fixtures used by other specs survive.
function signalBundle() {
  const script = `import json, sys, uuid
sys.path[:0] = ['../src', '../tests']
from test_signal_bundle import fixture, signed
from easyrag.retrieval.evidence_pack import fingerprint
b = fixture()
d = fingerprint(uuid.uuid4().hex)
i = 'inc-1700000100-' + fingerprint(['online-boutique', 1700000100, d])[:12]
b['incident_id'] = i
b['telemetry']['sha256'] = d
for m in b['metric_summaries']:
    m['source_incident_id'] = i
    m['summary_id'] = 'metric-lifecycle-' + uuid.uuid4().hex
    m['raw_ref'] = 'telemetry:sha256:' + d + '#column=checkoutservice_latency'
print(json.dumps(signed(b), ensure_ascii=False))`;
  return JSON.parse(
    execFileSync(
      process.env.TRACERAG_TEST_PYTHON || "python",
      ["-X", "utf8", "-c", script],
      {
        encoding: "utf8",
      },
    ),
  );
}

async function context(request) {
  const bootstrap = await (await request.get("/api/bootstrap")).json();
  return {
    bootstrap,
    headers: {
      "X-Console-Token": bootstrap.token,
      Origin: "http://127.0.0.1:8766",
    },
  };
}

async function importedEvent(request, headers, bundle = signalBundle()) {
  const response = await request.post("/api/events", {
    headers,
    data: bundle,
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).event_id;
}

async function generatedOrder(request, ctx, eventId) {
  const response = await request.post("/api/runs", {
    headers: ctx.headers,
    data: {
      event_id: eventId,
      query: "checkoutservice latency 当前异常如何排查",
      retrieval: ctx.bootstrap.retrieval,
      generation: ctx.bootstrap.generation,
    },
  });
  expect(response.ok()).toBeTruthy();
  const job = await response.json();
  let completed;
  await expect
    .poll(
      async () => {
        completed = await (await request.get(`/api/jobs/${job.id}`)).json();
        return completed.status;
      },
      { timeout: 30000 },
    )
    .toBe("completed");
  return completed.result.generation_run_id;
}

async function cleanup(request, headers, eventId, orderIds = [], caseId) {
  for (const id of orderIds)
    await request.delete(`/api/orders/${id}`, { headers });
  if (caseId)
    await request.delete(`/api/knowledge/cases/${encodeURIComponent(caseId)}`, {
      headers,
    });
  if (eventId) await request.delete(`/api/events/${eventId}`, { headers });
}

test("event deletion requires confirmation and retains its generated order", async ({
  page,
  request,
}) => {
  const ctx = await context(request);
  const eventId = await importedEvent(request, ctx.headers);
  const orderId = await generatedOrder(request, ctx, eventId);
  const staleReads = [];
  try {
    await page.goto("/");
    await page.locator("#event").selectOption(eventId);
    await expect(
      page.getByRole("button", { name: "删除事件", exact: true }),
    ).toBeVisible();
    page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("不可撤回");
      expect(dialog.message()).toContain("已生成工单");
      dialog.dismiss();
    });
    await page.getByRole("button", { name: "删除事件", exact: true }).click();
    expect((await request.get(`/api/events/${eventId}`)).ok()).toBeTruthy();
    await expect(page.locator("#event")).toHaveValue(eventId);
    page.once("dialog", (dialog) => dialog.accept());
    const deleted = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/events/${eventId}`) &&
        response.request().method() === "DELETE",
    );
    await page.getByRole("button", { name: "删除事件", exact: true }).click();
    expect((await deleted).ok()).toBeTruthy();
    page.on("request", (req) => {
      if (
        req.method() === "GET" &&
        req.url().endsWith(`/api/events/${eventId}`)
      )
        staleReads.push(req.url());
    });
    await expect(page.locator("#event")).toHaveValue("");
    await expect(
      page.getByRole("status").filter({ hasText: "已删除事件" }),
    ).toContainText("释放");
    expect((await request.get(`/api/events/${eventId}`)).status()).toBe(404);
    expect((await request.get(`/api/orders/${orderId}`)).ok()).toBeTruthy();
    await page
      .getByRole("button", { name: "工单与复核", exact: false })
      .first()
      .click();
    await page.locator("#order").selectOption(orderId);
    await expect(
      page.getByRole("heading", { name: "草稿与人工修订" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "运行日志", exact: false })
      .first()
      .click();
    await expect(
      page.getByRole("heading", { name: "运行日志", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".mode-pill")).toContainText("离线");
    expect(staleReads).toEqual([]);
  } finally {
    await cleanup(request, ctx.headers, eventId, [orderId]);
  }
});

test("deleting the selected order resets draft and comparison state only for removed IDs", async ({
  page,
  request,
}) => {
  const ctx = await context(request);
  const eventId = await importedEvent(request, ctx.headers);
  const first = await generatedOrder(request, ctx, eventId);
  const second = await generatedOrder(request, ctx, eventId);
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "实验记录", exact: true }).click();
    await page.locator(`.compare-select input[value="${first}"]`).check();
    await page.locator(`.compare-select input[value="${second}"]`).check();
    await page.getByRole("button", { name: "生成对照表" }).click();
    await expect(
      page.getByRole("columnheader", { name: "比较项" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "工单与复核", exact: false })
      .first()
      .click();
    await page.locator("#order").selectOption(first);
    await expect(page.locator("#claim-0")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "删除工单", exact: true }),
    ).toBeVisible();
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.getByRole("button", { name: "删除工单", exact: true }).click();
    await expect(page.locator("#claim-0")).toBeVisible();
    page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("修订");
      dialog.accept();
    });
    await page.getByRole("button", { name: "删除工单", exact: true }).click();
    await expect(page.locator("#order")).toHaveValue("");
    await expect(page.getByRole("heading", { name: "选择工单" })).toBeVisible();
    await expect(page.locator("#claim-0")).toHaveCount(0);
    expect((await request.get(`/api/orders/${first}`)).status()).toBe(404);
    expect((await request.get(`/api/orders/${second}`)).ok()).toBeTruthy();
    await page.getByRole("button", { name: "实验记录", exact: true }).click();
    await expect(
      page.getByRole("columnheader", { name: "比较项" }),
    ).toHaveCount(0);
    await expect(
      page.locator(`.compare-select input[value="${first}"]`),
    ).toHaveCount(0);
    await expect(
      page.locator(`.compare-select input[value="${second}"]`),
    ).toBeChecked();
  } finally {
    await cleanup(request, ctx.headers, eventId, [first, second]);
  }
});

test("published case details are visible and removal refreshes the current inventory", async ({
  page,
  request,
}) => {
  const ctx = await context(request);
  const eventId = await importedEvent(request, ctx.headers);
  const incident = await (await request.get(`/api/events/${eventId}`)).json();
  const caseId = "case-browser-lifecycle-" + randomUUID().replaceAll("-", "");
  const title = "LIFECYCLE SYNTHETIC CASE " + caseId.slice(-6);
  try {
    const admitted = await request.post("/api/knowledge/cases", {
      headers: ctx.headers,
      data: {
        case: {
          schema_version: "1.0",
          case_id: caseId,
          source_incident_id: incident.incident_id,
          system: incident.system,
          title,
          symptoms: ["Synthetic browser fixture only"],
          rca_candidates: [],
          verified_root_cause:
            "Fixture assertion, no real operational confirmation",
          actions: [
            "Synthetic fixture action, never performed on a real system",
          ],
          evidence_refs: ["synthetic:browser-lifecycle"],
          human_verified: true,
          verified_by: "BROWSER_TEST_NOT_HUMAN",
          verified_at: new Date().toISOString(),
          source: "synthetic browser test only",
          raw_ref: "synthetic:browser-lifecycle",
        },
        outcome: "Synthetic test outcome; not a research sample",
        outcome_evidence: "synthetic:browser-lifecycle",
        attestation: true,
      },
    });
    expect(admitted.ok()).toBeTruthy();
    const before = await (await request.get("/api/knowledge")).json();
    await page.goto("/");
    await page.getByRole("button", { name: "知识与案例", exact: true }).click();
    const card = page.locator(".case-card").filter({ hasText: title });
    await expect(card).toContainText("BROWSER_TEST_NOT_HUMAN");
    await expect(card).toContainText(incident.incident_id);
    await expect(card).toContainText("Synthetic test outcome");
    await expect(page.locator(".case-inventory")).toContainText("自报");
    await mkdir("../outputs/console-qa", { recursive: true });
    await page.screenshot({
      path: "../outputs/console-qa/lifecycle-cases-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: "../outputs/console-qa/lifecycle-cases-mobile.png",
      fullPage: true,
    });
    page.once("dialog", (dialog) => dialog.dismiss());
    await card.getByRole("button", { name: "删除案例", exact: true }).click();
    await expect(card).toBeVisible();
    page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("历史知识版本");
      expect(dialog.message()).toContain("快照");
      dialog.accept();
    });
    await card.getByRole("button", { name: "删除案例", exact: true }).click();
    await expect(card).toHaveCount(0);
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/knowledge")).json()).counts.case,
      )
      .toBe(before.counts.case - 1);
    await expect(
      page.locator(".inventory div").nth(1).locator("strong"),
    ).toHaveText(String(before.counts.case - 1));
  } finally {
    await cleanup(request, ctx.headers, eventId, [], caseId);
  }
});

test("visible troubleshooting measures save as a revision while the original remains unchanged", async ({
  page,
  request,
}) => {
  const ctx = await context(request);
  const eventId = await importedEvent(request, ctx.headers);
  let orderId;
  try {
    await page.goto("/");
    await page.locator("#event").selectOption(eventId);
    await page.getByRole("button", { name: "运行检索与工单" }).click();
    await expect(
      page.getByRole("button", { name: "删除事件", exact: true }),
    ).toBeDisabled();
    await expect(page.getByRole("heading", { name: "本次结果" })).toBeVisible({
      timeout: 30000,
    });
    await expect(
      page.getByRole("heading", { name: "排查措施", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".troubleshooting-step").first()).toContainText(
      "未执行",
    );
    await expect(
      page.locator(".troubleshooting-step .citation").first(),
    ).toBeVisible();
    await mkdir("../outputs/console-qa", { recursive: true });
    await page.screenshot({
      path: "../outputs/console-qa/lifecycle-measures-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: "../outputs/console-qa/lifecycle-measures-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page
      .getByRole("button", { name: "审核工单", exact: false })
      .first()
      .click();
    orderId = await page.locator("#order").inputValue();
    const original = await (await request.get(`/api/orders/${orderId}`)).json();
    const edited =
      "自动化测试措施：只读比较 checkoutservice latency 的参考与观测窗口，核对同一指标及引用。";
    await page
      .getByRole("textbox", { name: "排查措施 1 · 未执行", exact: true })
      .fill(edited);
    await page
      .getByLabel("复核人", { exact: true })
      .fill("BROWSER_TEST_NOT_HUMAN");
    await page
      .getByLabel("复核意见", { exact: true })
      .fill("Synthetic UI fixture only; not independent human verification.");
    const reviewResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/orders/${orderId}/reviews`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存人工修订", exact: false })
      .click();
    const review = await reviewResponse;
    expect(review.ok(), await review.text()).toBeTruthy();
    await expect(
      page.getByRole("status").filter({ hasText: "已保存新的人工修订版本。" }),
    ).toBeVisible();
    const saved = await (await request.get(`/api/orders/${orderId}`)).json();
    expect(saved.record).toEqual(original.record);
    expect(saved.revisions.at(-1).draft.verification_steps[0].text).toBe(
      edited,
    );
    expect(saved.revisions.at(-1).actions_executed).toBe(false);
  } finally {
    await cleanup(request, ctx.headers, eventId, orderId ? [orderId] : []);
  }
});

test("a retained order previews its own image when a duplicate import remains", async ({
  page,
  request,
}) => {
  const ctx = await context(request);
  const bundle = signalBundle();
  const eventId = await importedEvent(request, ctx.headers, bundle);
  const duplicate = await importedEvent(request, ctx.headers, bundle);
  let orderId;
  const imageData = execFileSync(
    process.env.TRACERAG_TEST_PYTHON || "python",
    [
      "-c",
      "import base64, io; from PIL import Image; b=io.BytesIO(); Image.new('RGB',(2,2),'red').save(b,format='PNG'); print(base64.b64encode(b.getvalue()).decode())",
    ],
    { encoding: "utf8" },
  ).trim();
  const sha = createHash("sha256")
    .update(Buffer.from(imageData, "base64"))
    .digest("hex");
  try {
    const attached = await request.post(`/api/events/${eventId}/evidence`, {
      headers: ctx.headers,
      data: {
        documents: [
          {
            schema_version: "1.0",
            knowledge_id: "image-lifecycle-" + randomUUID(),
            knowledge_type: "image",
            title: "Synthetic checkoutservice image",
            content:
              "checkoutservice Pod restart observed; synthetic image caption only",
            source: "synthetic browser fixture",
            raw_ref: "asset:" + sha,
            metadata: {
              system: bundle.system,
              source_incident_id: bundle.incident_id,
              window_start_utc: "2023-11-14T22:14:00Z",
              window_end_utc: bundle.analysis.evidence_cutoff_utc,
              asset_sha256: sha,
              derivation: "Synthetic fixture caption",
            },
          },
        ],
        assets: [{ sha256: sha, base64: imageData }],
      },
    });
    expect(attached.ok(), await attached.text()).toBeTruthy();
    const imageCtx = structuredClone(ctx);
    imageCtx.bootstrap.retrieval.sources = Object.fromEntries(
      Object.keys(ctx.bootstrap.retrieval.sources).map((key) => [
        key,
        key === "image",
      ]),
    );
    orderId = await generatedOrder(request, imageCtx, eventId);
    await page.goto("/");
    await page.locator("#event").selectOption(eventId);
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "删除事件", exact: true }).click();
    await expect(page.locator("#event")).toHaveValue("");
    expect((await request.get(`/api/events/${duplicate}`)).ok()).toBeTruthy();
    await page
      .getByRole("button", { name: "工单与复核", exact: false })
      .first()
      .click();
    await page.locator("#order").selectOption(orderId);
    const preview = page.getByRole("img", {
      name: "证据原始图像，解释以图注和来源为准",
    });
    await expect(preview).toBeVisible();
    await expect
      .poll(() =>
        preview.evaluate((img) => img.complete && img.naturalWidth > 0),
      )
      .toBe(true);
    await expect(preview).toHaveAttribute(
      "src",
      `/api/orders/${orderId}/assets/${sha}`,
    );
  } finally {
    await cleanup(request, ctx.headers, eventId, orderId ? [orderId] : []);
    await cleanup(request, ctx.headers, duplicate);
  }
});
