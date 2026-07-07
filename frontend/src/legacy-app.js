const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const state = {
  groups: [], activity: '01', subject: '01', trial: '1',
  alignment: null, fusion: null,
  colors: ['#6C7BFF', '#26C6A2', '#F5A45D', '#DA72D6']
};

const SCENE_NAMES = {
  '01': '稳态运行场景',
  '02': '启停切换场景',
  '03': '搬运作业场景',
  '04': '运输流转场景',
  '05': '负载变化场景',
  '06': '冲击扰动场景',
  '07': '横向转运场景'
};

function sceneName(group) {
  return SCENE_NAMES[group?.id] || SCENE_NAMES[group?.name] || group?.name || group;
}

function sampleLabel(sample) {
  return `边缘节点 ${sample.subject} · 第 ${sample.trial} 次`;
}

function sourceWording(text) {
  return String(text || '').replaceAll('模态', '数据源');
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : null
  });
  if (!response.ok) throw new Error((await response.json()).error || response.statusText);
  return response.json();
}

function selection() {
  return {activity: state.activity, subject: state.subject, trial: state.trial};
}

function setStatus(text, hint, color = '#f0a04b') {
  $('#globalStatus').textContent = text;
  $('#globalStatus').style.color = color;
  $('#statusHint').textContent = hint;
}

function legend(element, series) {
  element.innerHTML = series.map(item =>
    `<span><i style="background:${item.color}"></i>${item.name}</span>`
  ).join('');
}

// 所有对齐与融合时序图都使用相对于样本起点的真实时间（秒）。
function chart(canvas, series, options = {}) {
  const dpr = devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 300), height = Math.max(rect.height, 120);
  canvas.width = width * dpr; canvas.height = height * dpr;
  const context = canvas.getContext('2d'); context.scale(dpr, dpr);
  context.clearRect(0, 0, width, height);
  const pad = {left: 36, right: 14, top: 15, bottom: 26};
  context.strokeStyle = '#edf0f5'; context.lineWidth = 1;
  context.font = '9px sans-serif'; context.fillStyle = '#9aa2b2';
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (height - pad.top - pad.bottom) * i / 4;
    context.beginPath(); context.moveTo(pad.left, y); context.lineTo(width - pad.right, y); context.stroke();
    context.fillText((1 - i / 4).toFixed(2), 3, y + 3);
  }
  const all = series.flatMap(item => item.points);
  if (!all.length) return;
  const xMin = Math.min(...all.map(point => point[0]));
  const xMax = Math.max(...all.map(point => point[0]));
  series.forEach(item => {
    context.beginPath();
    item.points.forEach((point, index) => {
      const x = pad.left + (point[0] - xMin) / Math.max(xMax - xMin, 0.001) * (width - pad.left - pad.right);
      const y = pad.top + (1 - point[1]) * (height - pad.top - pad.bottom);
      index ? context.lineTo(x, y) : context.moveTo(x, y);
    });
    context.strokeStyle = item.color; context.lineWidth = options.bold ? 2.2 : 1.35;
    context.globalAlpha = options.alpha || 0.9; context.stroke();
  });
  context.globalAlpha = 1;
  const unit = options.unit || 's';
  context.textAlign = 'left'; context.fillText(`${xMin.toFixed(1)} ${unit}`, pad.left, height - 7);
  context.textAlign = 'center'; context.fillText(`${((xMin + xMax) / 2).toFixed(1)} ${unit}`, (pad.left + width - pad.right) / 2, height - 7);
  context.textAlign = 'right'; context.fillText(`${xMax.toFixed(2)} ${unit}`, width - pad.right, height - 7);
  context.textAlign = 'left';
}

function metrics(element, items) {
  element.innerHTML = items.map(item =>
    `<div class="metric"><span>${item.name}</span><b>${item.value}${item.unit ? ` <small>${item.unit}</small>` : ''}</b><small>${item.trend}</small></div>`
  ).join('');
}

function afterLayout(callback) {
  requestAnimationFrame(() => requestAnimationFrame(callback));
}

function renderPreviewChart(data) {
  chart($('#previewChart'), data.series);
  legend($('#previewLegend'), data.series);
}

function renderAlignCharts(data) {
  chart($('#alignChart'), data.aligned);
  legend($('#alignLegend'), data.aligned);
}

function renderFusionCharts(data) {
  chart($('#fusionChart'), [{name: '融合特征', color: '#16bfa6', points: data.fusedSeries}], {bold: true});
  donut($('#donutChart'), data.contribution);
}

async function init() {
  try {
    const data = await api('/api/dataset');
    state.groups = data.groups;
    $('#totalSamples').textContent = data.total; $('#modalities').textContent = data.modalities;
    $('#groupCount').textContent = data.groups.length;
    renderGroups(); await chooseGroup(data.groups[0].id); renderHistory();
  } catch (error) { setStatus('数据错误', error.message, '#e05c67'); }
}

function renderGroups() {
  $('#groups').innerHTML = state.groups.map(group =>
    `<button class="group-card ${group.id === state.activity ? 'active' : ''}" data-id="${group.id}"><i></i><b>${sceneName(group)}</b><small>${group.count} 组同步样本</small></button>`
  ).join('');
  $$('.group-card').forEach(card => card.onclick = () => chooseGroup(card.dataset.id));
}

async function chooseGroup(id) {
  state.activity = id;
  const group = state.groups.find(item => item.id === id);
  state.subject = group.samples[0].subject; state.trial = group.samples[0].trial;
  renderGroups();
  $('#sampleSelect').innerHTML = group.samples.map(sample =>
    `<option value="${sample.subject}|${sample.trial}">${sampleLabel(sample)}</option>`
  ).join('');
  $('#selectionName').textContent = `${sceneName(group)} · 边缘节点 ${state.subject}`;
  resetStages(); await preview();
}

$('#sampleSelect').onchange = async event => {
  [state.subject, state.trial] = event.target.value.split('|');
  $('#selectionName').textContent = `${sceneName(state.groups.find(item => item.id === state.activity))} · 边缘节点 ${state.subject}`;
  resetStages(); await preview();
};

function resetStages() {
  state.alignment = null; state.fusion = null;
  $('#alignResults').classList.add('hidden'); $('#fusionResults').classList.add('hidden');
  $('#fusionPanel').classList.add('disabled'); $('#fusionPanel').classList.remove('ready');
  $('#fuseBtn').disabled = true; setStatus('待处理', '点击“开始对齐”');
}

async function preview() {
  setStatus('读取中', '正在加载多源异构数据');
  try {
    const data = await api('/api/preview', selection());
    afterLayout(() => renderPreviewChart(data));
    $('#previewMeta').textContent = `${data.rows} 个采样点 · ${data.duration}s`;
    setStatus('待处理', '数据已就绪');
  } catch (error) { setStatus('读取失败', error.message, '#e05c67'); }
}

function animateProgress(box, percentElement, textElement, steps) {
  box.classList.remove('hidden'); let progress = 0; const bar = box.querySelector('.progress i');
  return new Promise(resolve => {
    const timer = setInterval(() => {
      progress = Math.min(94, progress + Math.random() * 8 + 2); bar.style.width = `${progress}%`;
      percentElement.textContent = `${Math.floor(progress)}%`;
      if (textElement) textElement.textContent = steps[Math.min(steps.length - 1, Math.floor(progress / (100 / steps.length)))];
      if (progress >= 94) { clearInterval(timer); resolve(() => { bar.style.width = '100%'; percentElement.textContent = '100%'; }); }
    }, 110);
  });
}

$('#alignBtn').onclick = async () => {
  const button = $('#alignBtn'); button.disabled = true; $('.pipeline').classList.add('running');
  $('#alignEngine').textContent = '计算中…'; setStatus('正在对齐', '构建统一时间轴', '#6c63ff');
  const progress = animateProgress($('#alignProgress'), $('#alignPct'), $('#alignProgressText'),
    ['正在读取多源数据时间戳…', '建立统一基准时间轴…', '执行跨数据源时间匹配…', '计算对齐质量指标…']);
  try {
    const [data, finish] = await Promise.all([api('/api/align', selection()), progress]); finish();
    await new Promise(resolve => setTimeout(resolve, 300)); state.alignment = data;
    $('.pipeline').classList.remove('running'); $('#alignEngine').textContent = '对齐完成';
    $('#alignedPoints').textContent = `${data.summary.points} 个对齐点`;
    $('#beforeRmse').textContent = `${data.summary.beforeRmse} ms`; $('#afterRmse').textContent = `${data.summary.afterRmse} ms`;
    metrics($('#alignMetrics'), data.metrics);
    $('#alignResults').classList.remove('hidden'); $('#fusionPanel').classList.remove('disabled'); $('#fusionPanel').classList.add('ready');
    afterLayout(() => renderAlignCharts(data));
    $('#fuseBtn').disabled = false; setStatus('对齐完成', `成功率 ${data.summary.successRate}%`, '#16bfa6');
    setTimeout(() => $('#alignProgress').classList.add('hidden'), 500);
  } catch (error) { setStatus('对齐失败', error.message, '#e05c67'); }
  finally { button.disabled = false; }
};

$$('#weights input').forEach(input => input.oninput = () => { input.nextElementSibling.textContent = `${input.value}%`; });

$('#fuseBtn').onclick = async () => {
  const button = $('#fuseBtn'); button.disabled = true; $('#fusionResults').classList.add('hidden');
  $('#fusionAnimation').classList.remove('hidden'); setStatus('正在融合', '特征归一化与加权聚合', '#16bfa6');
  let progress = 0; const timer = setInterval(() => { $('#fusionPct').textContent = `${Math.min(94, progress += 4)}%`; }, 90);
  try {
    const weights = $$('#weights input').map(input => +input.value);
    const data = await api('/api/fuse', {...selection(), weights});
    await new Promise(resolve => setTimeout(resolve, 1800)); clearInterval(timer); $('#fusionPct').textContent = '100%';
    await new Promise(resolve => setTimeout(resolve, 250)); $('#fusionAnimation').classList.add('hidden');
    state.fusion = data; renderFusion(data); saveHistory(data, weights); $('#fusionResults').classList.remove('hidden');
    afterLayout(() => renderFusionCharts(data));
    setStatus('处理完成', '结果可导出', '#16bfa6');
    $('#export').href = `/api/export?subject=${state.subject}&activity=${state.activity}&trial=${state.trial}`;
    setTimeout(() => $('#fusionResults').scrollIntoView({behavior: 'smooth', block: 'start'}), 100);
  } catch (error) { clearInterval(timer); setStatus('融合失败', error.message, '#e05c67'); }
  finally { button.disabled = false; }
};

function renderFusion(data) {
  const decision = data.decision;
  $('#decisionCard').className = `decision-card ${decision.level}`;
  $('#decisionLabel').textContent = decision.label; $('#decisionScore').textContent = decision.score.toFixed(4);
  $('#decisionConfidence').textContent = `${decision.confidence}%`; $('#decisionSources').textContent = decision.mainSources.join('、');
  $('#decisionAlignment').textContent = `${decision.alignment}（${decision.alignmentRate}%）`;
  $('#decisionRule').textContent = decision.rule; $('#decisionReason').textContent = sourceWording(decision.reason);
  $('#decisionNote').textContent = '主要贡献数据源由各数据源异常得分与融合权重共同决定，并非仅由权重决定。';

  $('#coreBeforeRmse').textContent = `${data.summary.beforeRmse} ms`; $('#coreAfterRmse').textContent = `${data.summary.afterRmse} ms`;
  $('#coreFusionScore').textContent = decision.score.toFixed(4); $('#coreConfidence').textContent = `${decision.confidence}%`;
  $('#coreSource').textContent = decision.mainSources.join('、'); $('#coreSuccessRate').textContent = `${data.summary.successRate}%`;
  $('#fusionAxisLabel').textContent = `真实时间 0～${data.summary.duration.toFixed(2)} s`;

  $('#donutLegend').innerHTML = data.contribution.map((item, index) =>
    `<div><i style="background:${state.colors[index]}"></i>${item.name} ${item.value}%</div>`
  ).join('');
  $('#contributionRows').innerHTML = data.contribution.map(item =>
    `<div class="contribution-row"><span>${item.name}</span><span>${item.weight}%</span><span>${item.anomalyScore.toFixed(4)}</span><b>${item.contribution.toFixed(4)}</b></div>`
  ).join('');
  $('#fusionMetrics').innerHTML = data.metrics.map(item =>
    `<div class="table-row"><span>${item.name}</span><b>${item.value} ${item.unit}</b><small>${item.trend}</small></div>`
  ).join('');
}

function donut(canvas, data) {
  const dpr = devicePixelRatio || 1, rect = canvas.getBoundingClientRect(), width = rect.width, height = rect.height;
  canvas.width = width * dpr; canvas.height = height * dpr;
  const context = canvas.getContext('2d'); context.scale(dpr, dpr);
  const centerX = width * 0.34, centerY = height * 0.5, radius = Math.min(width, height) * 0.31;
  context.lineWidth = 24; let angle = -Math.PI / 2;
  data.forEach((item, index) => {
    const next = item.value / 100 * Math.PI * 2; context.beginPath(); context.arc(centerX, centerY, radius, angle, angle + next);
    context.strokeStyle = state.colors[index]; context.stroke(); angle += next;
  });
  context.fillStyle = '#1c2333'; context.font = 'bold 18px sans-serif'; context.textAlign = 'center'; context.fillText('100%', centerX, centerY + 3);
  context.font = '9px sans-serif'; context.fillStyle = '#929bad'; context.fillText('综合贡献', centerX, centerY + 20);
}

function historyData() {
  try { return JSON.parse(localStorage.getItem('fusionHistory') || '[]'); } catch { return []; }
}

function saveHistory(data, weights) {
  const group = state.groups.find(item => item.id === state.activity);
  const records = historyData();
  records.unshift({
    time: new Date().toLocaleString('zh-CN'), group: sceneName(group) || state.activity,
    subject: state.subject, trial: state.trial, label: data.decision.label,
    score: data.decision.score, confidence: data.decision.confidence,
    beforeRmse: data.summary.beforeRmse, afterRmse: data.summary.afterRmse,
    successRate: data.summary.successRate, sources: data.decision.mainSources, weights
  });
  localStorage.setItem('fusionHistory', JSON.stringify(records.slice(0, 30))); renderHistory();
}

function renderHistory() {
  const records = historyData();
  $('#historyList').innerHTML = records.length ? records.map(item =>
    `<article class="history-item"><div><b>${SCENE_NAMES[item.group] || item.group} · 边缘节点 ${item.subject}</b><span class="history-label">${item.label}</span></div><small>${item.time}</small><p>融合得分 ${item.score.toFixed(4)} · 置信度 ${item.confidence}% · 对齐成功率 ${item.successRate}%</p><p>RMSE ${item.beforeRmse} → ${item.afterRmse} ms · 主要贡献数据源：${item.sources.join('、')}</p></article>`
  ).join('') : '<div class="history-empty">暂无历史记录<br><small>完成一次对齐融合后将在这里显示</small></div>';
}

$('#historyBtn').onclick = () => { renderHistory(); $('#historyDrawer').classList.remove('hidden'); $('#historyBtn').classList.add('active'); $('#workspaceBtn').classList.remove('active'); };
$('#historyClose').onclick = () => { $('#historyDrawer').classList.add('hidden'); $('#historyBtn').classList.remove('active'); $('#workspaceBtn').classList.add('active'); };
$('#workspaceBtn').onclick = () => $('#historyClose').click();
$('#clearHistory').onclick = () => { localStorage.removeItem('fusionHistory'); renderHistory(); };

window.addEventListener('resize', () => {
  if (state.alignment) renderAlignCharts(state.alignment);
  if (state.fusion) renderFusionCharts(state.fusion);
});

init();
