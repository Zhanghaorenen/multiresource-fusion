<template>
  <aside>
    <div class="brand"><span class="brand-mark">M</span><div><b>MEx Fusion</b><small>面向产业链供应链的多源异构数据时空对齐与融合系统</small></div></div>
    <nav><button class="active" id="workspaceBtn">⌁<span>工作台</span></button><button id="historyBtn">▤<span>历史记录</span></button></nav>
    <div class="side-status"><i></i><div><b>计算引擎在线</b><small>本地实时处理</small></div></div>
  </aside>
  <main>
    <header><div><h1>多源异构产业链供应链数据对齐与融合</h1><p>边缘业务场景下多源异构感知数据的一体化可视分析</p></div><div class="header-actions"><span class="dataset-pill">Supply Chain Edge Data</span><a id="export" class="icon-btn" title="导出结果">⇩</a></div></header>

    <section class="stats">
      <div><span>同步样本</span><strong id="totalSamples">—</strong><small>组同步记录</small></div>
      <div><span>异构数据源</span><strong id="modalities">4</strong><small>边缘感知源</small></div>
      <div><span>业务场景</span><strong id="groupCount">7</strong><small>业务状态类别</small></div>
      <div><span>处理状态</span><strong class="status-text" id="globalStatus">待处理</strong><small id="statusHint">请选择边缘业务场景</small></div>
    </section>

    <section class="panel selection-panel">
      <div class="panel-title"><div><span class="step">01</span><div><h2>选择边缘业务场景</h2><p>多源异构供应链感知数据的同步记录</p></div></div><label>具体样本 <select id="sampleSelect"></select></label></div>
      <div class="groups" id="groups"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>
      <div class="preview-meta"><span id="selectionName">等待选择</span><span id="previewMeta">—</span></div>
      <div class="chart-wrap preview-chart"><canvas id="previewChart"></canvas><div class="legend" id="previewLegend"></div></div>
    </section>

    <section class="panel process-panel" id="alignPanel">
      <div class="panel-title"><div><span class="step purple">02</span><div><h2>时空对齐</h2><p>基于统一时间轴的多源异构数据匹配</p></div></div><button class="primary" id="alignBtn">开始对齐 <span>→</span></button></div>
      <div class="pipeline">
        <div class="source-nodes"><i style="--c:#DA72D6">温度图片</i><i style="--c:#6C7BFF">振动数据</i><i style="--c:#26C6A2">音频数据</i><i style="--c:#F5A45D">视频数据</i></div>
        <div class="flow-lines"><b></b><b></b><b></b><b></b></div><div class="core"><span>⌖</span><b>时空对齐引擎</b><small id="alignEngine">等待启动</small></div><div class="flow-out"><b></b></div><div class="result-node"><span>✓</span><b>统一时间轴</b><small id="alignedPoints">— 个对齐点</small></div>
      </div>
      <div class="progress-box hidden" id="alignProgress"><div><b id="alignProgressText">正在建立时间索引…</b><span id="alignPct">0%</span></div><div class="progress"><i></i></div></div>
      <div class="results hidden" id="alignResults">
        <div class="result-head"><h3>对齐结果可视化</h3><div class="before-after"><span>对齐前 RMSE <b id="beforeRmse">—</b></span><em>→</em><span>对齐后 RMSE <b id="afterRmse">—</b></span></div></div>
        <div class="chart-wrap"><canvas id="alignChart"></canvas><div class="legend" id="alignLegend"></div></div>
        <div class="metric-grid" id="alignMetrics"></div>
      </div>
    </section>

    <section class="panel process-panel disabled" id="fusionPanel">
      <div class="panel-title"><div><span class="step cyan">03</span><div><h2>多源异构数据融合</h2><p>多源特征归一化与自适应加权融合</p></div></div><button class="primary cyan-btn" id="fuseBtn" disabled>开始融合 <span>→</span></button></div>
      <div class="weight-row" id="weights">
        <label>振动数据 <input type="range" min="0" max="100" value="25"><b>25%</b></label><label>音频数据 <input type="range" min="0" max="100" value="25"><b>25%</b></label><label>视频数据 <input type="range" min="0" max="100" value="25"><b>25%</b></label><label>温度图片 <input type="range" min="0" max="100" value="25"><b>25%</b></label>
      </div>
      <div class="fusion-animation hidden" id="fusionAnimation"><div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div><div class="orb o4"></div><div class="fusion-core">融合中<small id="fusionPct">0%</small></div></div>
      <div class="results hidden" id="fusionResults">
        <div class="decision-card" id="decisionCard">
          <div class="decision-main"><span>最终判别结果</span><strong id="decisionLabel">—</strong><small>融合响应强度判别</small></div>
          <div class="decision-detail"><div><span>融合得分</span><b id="decisionScore">—</b></div><div><span>融合置信度</span><b id="decisionConfidence">—</b></div><div><span>主要贡献数据源</span><b id="decisionSources">—</b></div><div><span>时空对齐结果</span><b id="decisionAlignment">—</b></div></div>
          <div class="decision-explain"><b>判别规则</b><span id="decisionRule">融合得分 &lt; 0.50：正常；0.50 ≤ 融合得分 &lt; 0.70：疑似异常；融合得分 ≥ 0.70：异常。</span><b>判别原因</b><span id="decisionReason">—</span></div>
          <p id="decisionNote">主要贡献数据源由各数据源异常得分与融合权重共同决定，并非仅由权重决定。</p>
        </div>
        <div class="fusion-summary core-summary"><div><span>对齐前 RMSE</span><strong id="coreBeforeRmse">—</strong><small>越小越好</small></div><div><span>对齐后 RMSE</span><strong id="coreAfterRmse">—</strong><small>越小越好</small></div><div><span>融合得分</span><strong id="coreFusionScore">—</strong><small>用于状态判别</small></div><div><span>融合置信度</span><strong id="coreConfidence">—</strong><small>越大响应越强</small></div><div><span>主要贡献数据源</span><strong id="coreSource">—</strong><small>权重 × 异常得分</small></div><div><span>对齐成功率</span><strong id="coreSuccessRate">—</strong><small>越大越好</small></div></div>
        <div class="fusion-grid"><div class="chart-card"><h3>融合特征时序曲线 <small id="fusionAxisLabel">真实时间（s）</small></h3><canvas id="fusionChart"></canvas></div><div class="chart-card"><h3>数据源贡献占比</h3><canvas id="donutChart"></canvas><div id="donutLegend" class="donut-legend"></div></div></div>
        <div class="result-head contribution-head"><h3>数据源贡献明细</h3><small>贡献值 = 融合权重 × 数据源异常得分</small></div>
        <div class="contribution-table"><div class="contribution-row table-header"><span>数据源名称</span><span>权重</span><span>异常得分</span><span>贡献值</span></div><div id="contributionRows"></div></div>
        <div class="result-head auxiliary-head"><h3>辅助质量指标</h3><small>用于补充分析，不直接决定最终判别</small></div><div class="metrics-table" id="fusionMetrics"></div>
      </div>
    </section>
    <footer>MEx Fusion Lab · 所有计算均在本机完成 · 数据不会离开当前设备</footer>
  </main>
  <section class="history-drawer hidden" id="historyDrawer"><div class="history-title"><div><h2>历史记录</h2><p>保存在当前浏览器中的对齐融合结果</p></div><button id="historyClose">×</button></div><div id="historyList" class="history-list"></div><button id="clearHistory" class="history-clear">清空历史记录</button></section>
</template>

<script setup>
import { onMounted } from 'vue';

onMounted(async () => {
  await import('./legacy-app.js');
});
</script>
