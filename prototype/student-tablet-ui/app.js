const view = document.querySelector('[data-view="login"]');
const appView = document.querySelector('[data-view="app"]');
const panels = document.querySelectorAll('[data-panel]');
const navItems = document.querySelectorAll('[data-nav]');
const toast = document.querySelector('#toast');
const uploadModal = document.querySelector('#upload-modal');
let toastTimer;

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add('is-visible');
  toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2600);
}

function showApp() {
  view.classList.remove('is-active');
  appView.classList.add('is-active');
  switchPanel('home');
}

function switchPanel(name) {
  panels.forEach((panel) => {
    const active = panel.dataset.panel === name;
    panel.hidden = !active;
    panel.classList.toggle('is-active', active);
  });
  navItems.forEach((item) => item.classList.toggle('is-active', item.dataset.nav === name));
}

function openUpload() {
  uploadModal.hidden = false;
  document.querySelector('#homework-file').focus();
}

function closeUpload() {
  uploadModal.hidden = true;
}

document.querySelector('#login-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const account = document.querySelector('#student-account');
  const password = document.querySelector('#student-password');
  const accountError = document.querySelector('#account-error');
  const passwordError = document.querySelector('#password-error');
  accountError.textContent = account.value.trim() ? '' : '请输入学生账号';
  passwordError.textContent = password.value.trim() ? '' : '请输入密码';
  if (!account.value.trim() || !password.value.trim()) return;
  showApp();
});

document.querySelector('[data-password-toggle]').addEventListener('click', (event) => {
  const password = document.querySelector('#student-password');
  const isPassword = password.type === 'password';
  password.type = isPassword ? 'text' : 'password';
  event.currentTarget.setAttribute('aria-label', isPassword ? '隐藏密码' : '显示密码');
  event.currentTarget.querySelector('span').textContent = isPassword ? '●' : '○';
});

navItems.forEach((item) => item.addEventListener('click', () => switchPanel(item.dataset.nav)));
document.querySelectorAll('[data-open-upload]').forEach((item) => item.addEventListener('click', openUpload));
document.querySelectorAll('[data-close-upload]').forEach((item) => item.addEventListener('click', closeUpload));
document.querySelectorAll('[data-toast]').forEach((item) => item.addEventListener('click', () => showToast(item.dataset.toast)));

document.querySelector('#homework-file').addEventListener('change', (event) => {
  const files = Array.from(event.target.files || []);
  document.querySelector('#upload-file-name').textContent = files.length ? `已选择 ${files.length} 张照片` : '还没有选择照片';
});

document.querySelector('[data-submit-upload]').addEventListener('click', () => {
  const fileInput = document.querySelector('#homework-file');
  if (!fileInput.files.length) {
    showToast('请先选择作业照片');
    return;
  }
  closeUpload();
  showToast('已收到作业照片，分析结果将在这里展示');
});

const nodeData = {
  overview: { subject: '学习概览', title: '我的知识地图', description: '继续完成作业和测评，图谱会逐步变得更完整。', progress: '—', width: '0%', status: 'focus', source: '作业与阶段测评' },
  equation: { subject: '数学 · 待加强', title: '一元一次方程', description: '最近 3 次练习中，有 2 次在“移项与合并同类项”处出错。', progress: '48%', width: '48%', status: 'focus', source: '最近 3 次作业' },
  past: { subject: '英语 · 巩固中', title: '一般过去时', description: '基本句型已经掌握，时间状语和特殊疑问句还需要再练习。', progress: '67%', width: '67%', status: 'learning', source: '最近 4 次作业' },
  reflection: { subject: '物理 · 已掌握', title: '光的反射', description: '最近一次测评表现稳定，可以尝试综合应用题。', progress: '86%', width: '86%', status: 'strong', source: '阶段测评与作业' },
  geometry: { subject: '数学 · 巩固中', title: '几何图形', description: '图形性质理解较好，证明步骤还需要保持完整。', progress: '61%', width: '61%', status: 'learning', source: '最近 2 次作业' },
  reading: { subject: '英语 · 已掌握', title: '阅读理解', description: '主旨和细节定位表现稳定，继续保持阅读习惯。', progress: '82%', width: '82%', status: 'strong', source: '最近 5 次作业' },
  force: { subject: '物理 · 待加强', title: '力与运动', description: '受力分析的图示还不稳定，建议先复习基础概念。', progress: '42%', width: '42%', status: 'focus', source: '最近 2 次作业' },
};

function selectNode(nodeId) {
  const data = nodeData[nodeId];
  if (!data) return;
  const detail = document.querySelector('#node-detail');
  document.querySelectorAll('.map-node').forEach((node) => node.classList.toggle('is-selected', node.dataset.node === nodeId));
  document.querySelector('#detail-subject').textContent = data.subject;
  document.querySelector('#detail-subject').style.color = data.status === 'focus' ? 'var(--purple)' : data.status === 'learning' ? '#d37a4f' : '#429973';
  document.querySelector('#detail-title').textContent = data.title;
  document.querySelector('#detail-description').textContent = data.description;
  document.querySelector('#detail-progress-value').textContent = data.progress;
  document.querySelector('#detail-progress-bar').style.width = data.width;
  document.querySelector('#detail-progress-bar').style.background = data.status === 'focus' ? 'var(--purple)' : data.status === 'learning' ? 'var(--orange)' : 'var(--green)';
  document.querySelector('.detail-status-dot').className = `detail-status-dot detail-status-dot--${data.status}`;
  document.querySelector('#detail-source-value').textContent = data.source;
  detail.dataset.status = data.status;
}

document.querySelectorAll('.map-node').forEach((node) => node.addEventListener('click', () => selectNode(node.dataset.node)));

document.querySelectorAll('[data-subject]').forEach((tab) => {
  if (!tab.classList.contains('subject-tab')) return;
  tab.addEventListener('click', () => {
    const subject = tab.dataset.subject;
    document.querySelectorAll('.subject-tab').forEach((item) => {
      const active = item === tab;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.map-node[data-subject]').forEach((node) => node.classList.toggle('is-hidden', subject !== 'all' && node.dataset.subject !== subject));
  });
});
