(function () {
  const SEVEN = ["15m", "1h", "2h", "4h", "1d", "1w", "1mo"];
  const EQUITY_SET = ["15m", "1h", "195m", "1d", "1w", "1mo"];

  function intervalsForCurrent() {
    const isCrypto = (window.current?.subject || el("subject").value).startsWith("crypto-");
    return isCrypto ? SEVEN : EQUITY_SET;
  }

  function includePerp() {
    return (window.current?.subject || el("subject").value).startsWith("crypto-");
  }

  async function createMultiSnapshot() {
    const subject_id = window.current?.subject || el("subject").value;
    return api("/v1/analysis-snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_id,
        intervals: intervalsForCurrent(),
        include_perpetual: includePerp(),
      }),
    });
  }

  async function checkSevenTimeframes() {
    clearError();
    el("analysis").innerHTML = "<p class='muted'>正在生成多周期快照（不调用模型，不消耗额度）…</p>";
    const ref = await createMultiSnapshot();
    const snap = await api(`/v1/analysis-snapshots/${ref.snapshot_id}`);
    el("analysis").innerHTML = `
      <p class="muted"><strong>公开市场上下文</strong> · 快照 ${snap.snapshot_id} · 证据 ${snap.evidence.length} 条 · 预设评估规则 ${snap.scenario_rules.length} 条 · 不调用模型</p>
      ${snap.timeframe_coverage
        .map(
          (c) =>
            `<div class="muted ${c.status !== "AVAILABLE" ? "bad" : ""}">${c.instrument_id} / ${c.interval} / ${c.role} / ${c.status} / history_complete=${c.history_complete} / 证据 ${c.evidence_ids.length} 条 ${
              c.missing_reason ? "· " + c.missing_reason : ""
            }</div>`
        )
        .join("")}
    `;
  }

  async function runRealAi() {
    clearError();
    const health = await api("/v1/health");
    if (!health.codex_cli_available) {
      throw { detail: "本机未检测到 codex CLI，真实 AI 分析未执行。" };
    }
    if (
      !window.confirm(
        `确认调用本机 Codex CLI（默认模型 ${health.real_ai_model}）？该操作只会在你本次点击后运行 1 次，并消耗 Codex/ChatGPT 额度。`
      )
    ) {
      return;
    }
    el("analysis").innerHTML = "<p class='muted'>正在调用本机 Codex CLI 生成多周期真实 AI 分析（最多等待约 6 分钟）…</p>";
    const snap = await createMultiSnapshot();
    const key = `real-ai-${snap.snapshot_id}`.slice(0, 64).padEnd(16, "0");
    const task = await api("/v1/ai-analysis-tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify({
        snapshot_id: snap.snapshot_id,
        provider: "CODEX_CLI",
        model: health.real_ai_model,
        prompt_version: "phase1-real-ai-v1",
      }),
    });
    for (let i = 0; i < 180; i++) {
      const cur = await api(`/v1/ai-analysis-tasks/${task.task_id}`);
      if (cur.status === "SUCCEEDED") {
        const [a, sem] = await Promise.all([
          api(`/v1/ai-analyses/${cur.analysis_id}`),
          api(`/v1/ai-analyses/${cur.analysis_id}/semantic-review`).catch(() => null),
        ]);
        if (typeof window.renderAnalysisResult === "function") {
          window.renderAnalysisResult(a, sem);
        } else {
          const levels = Object.fromEntries(a.key_levels.map((l) => [l.level_id, l]));
          el("analysis").innerHTML = `
            <p class="muted">真实模型：${a.audit.provider}/${a.audit.model} · 输入完整度：${a.confidence.score} · 成本：${a.audit.cost_currency} ${a.audit.cost}</p>
            ${a.timeframe_synthesis
              .map((t) => `<div class="muted"><strong>${t.instrument_id} ${t.interval} (${t.role})</strong>：${t.summary || t.missing_reason}</div>`)
              .join("")}
            ${a.scenarios
              .map(
                (s) => `<div class="scenario ${s.type}">
                <strong>${s.type}</strong>（规则 ${s.evaluation_rule_id || "N/A"}）：${s.summary}
                <div class="muted">关键位：${s.key_level_ids.map((id) => (levels[id] ? `${levels[id].kind}=${levels[id].price}` : id)).join("，")}</div>
              </div>`
              )
              .join("")}
          `;
        }
        return;
      }
      if (cur.status.startsWith("FAILED")) throw cur.error || cur;
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw { detail: "真实 AI 任务等待超时，请稍后查询任务状态。" };
  }

  let checkBtn = document.getElementById("snapshotCheck");
  if (!checkBtn) {
    const analyzeBtn = el("analyze");
    checkBtn = document.createElement("button");
    checkBtn.id = "snapshotCheck";
    checkBtn.textContent = "仅核对七周期快照（不调用模型）";
    analyzeBtn.insertAdjacentElement("beforebegin", checkBtn);
  }
  checkBtn.onclick = () => checkSevenTimeframes().catch(showError);

  let realBtn = document.getElementById("realAiAnalyze");
  if (!realBtn) {
    realBtn = document.createElement("button");
    realBtn.id = "realAiAnalyze";
    realBtn.textContent = "调用真实 AI（消耗 Codex/ChatGPT 额度）";
    realBtn.style.borderColor = "#f59e0b";
    checkBtn.insertAdjacentElement("afterend", realBtn);
  }
  realBtn.onclick = () => runRealAi().catch(showError);
})();
