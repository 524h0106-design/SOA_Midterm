const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem('ibanking-token') || '';
let profile = null;
let payment = null;
let currentTuition = null;
let studentList = [];
let timer = null;
let lookupDebounce = null;
const money = (amount) => new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(amount);
const statuses = { SUCCESS: 'Thành công', PENDING: 'Chờ xác thực', PROCESSING: 'Đang xử lý', FAILED: 'Thất bại', EXPIRED: 'Hết hạn', CANCELLED: 'Đã hủy', REFUNDED: 'Đã hoàn tác' };
const tuitionStatuses = { UNPAID: 'Chưa thanh toán', PAID: 'Đã thanh toán', RESERVED: 'Đang xử lý giao dịch' };

function notice(message) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}

function highlightSelectedStudent(sid) {
  const upper = (sid || '').toUpperCase();
  for (const row of document.querySelectorAll('.student-row')) {
    row.classList.toggle('selected', row.dataset.mssv === upper);
  }
}

function renderStudentList() {
  const tbody = $('student-list-body');
  tbody.replaceChildren();
  if (!studentList.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 4;
    td.className = 'muted';
    td.textContent = 'Chưa có danh sách sinh viên.';
    tr.append(td);
    tbody.append(tr);
    return;
  }
  const activeSid = (currentTuition?.student_id || $('student-id-input').value.trim()).toUpperCase();
  for (const item of studentList) {
    const tr = document.createElement('tr');
    const isPaid = item.status === 'PAID';
    tr.className = 'student-row' + (isPaid ? ' is-paid' : '') + (item.student_id === activeSid ? ' selected' : '');
    tr.dataset.mssv = item.student_id;

    const tdId = document.createElement('td');
    tdId.textContent = item.student_id;

    const tdName = document.createElement('td');
    tdName.textContent = item.student_name;

    const tdAmount = document.createElement('td');
    tdAmount.textContent = money(item.amount);

    const tdStatus = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'badge' + (isPaid ? ' success' : item.status === 'UNPAID' ? '' : ' failure');
    badge.textContent = tuitionStatuses[item.status] || item.status;
    tdStatus.append(badge);

    tr.append(tdId, tdName, tdAmount, tdStatus);
    tr.addEventListener('click', () => {
      $('student-id-input').value = item.student_id;
      highlightSelectedStudent(item.student_id);
      lookupTuition(item.student_id);
    });
    tbody.append(tr);
  }
}

const DEFAULT_MSSV_LIST = ['524H0101', '524H0102', '524H0103', '524H0104', '524H0105', '524H0106', '524H0107', '524H0108', '524H0109', '524H0110'];

async function loadStudentList() {
  try {
    const data = await api('/tuition');
    studentList = Array.isArray(data.items) ? data.items : [];
    renderStudentList();
  } catch (error) {
    if (error.status === 404) {
      try {
        const results = await Promise.all(
          DEFAULT_MSSV_LIST.map((sid) => api('/tuition/' + sid).catch(() => null))
        );
        studentList = results.filter(Boolean);
        if (studentList.length) {
          renderStudentList();
          return;
        }
      } catch {}
    }
    const tbody = $('student-list-body');
    tbody.replaceChildren();
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 4;
    td.className = 'muted';
    td.textContent = error.status === 503 ? 'Chưa kết nối được dịch vụ học phí.' : error.message;
    tr.append(td);
    tbody.append(tr);
  }
}

function clearTuitionState() {
  currentTuition = null;
  clearTimeout(lookupDebounce);
  $('tuition-error').textContent = '';
  $('tuition-details').hidden = true;
  $('payment-amount').textContent = '—';
  $('payment-remaining').textContent = '—';
  $('payment-eligibility-msg').textContent = 'Vui lòng chọn sinh viên từ danh sách hoặc nhập MSSV để tra cứu khoản học phí.';
  $('submit-payment-btn').disabled = true;
}

function updatePaymentEligibility() {
  if (!profile) {
    $('submit-payment-btn').disabled = true;
    return;
  }
  $('payment-balance').textContent = money(profile.balance);
  if (!currentTuition) {
    $('payment-amount').textContent = '—';
    $('payment-remaining').textContent = '—';
    $('submit-payment-btn').disabled = true;
    return;
  }
  $('payment-amount').textContent = money(currentTuition.amount);
  const remaining = profile.balance - currentTuition.amount;
  $('payment-remaining').textContent = remaining >= 0 ? money(remaining) : 'Không đủ số dư (' + money(remaining) + ')';

  const isUnpaid = currentTuition.status === 'UNPAID';
  const hasBalance = profile.balance >= currentTuition.amount;
  const termsOk = $('terms-accepted').checked;

  if (!isUnpaid) {
    $('payment-eligibility-msg').textContent = currentTuition.status === 'PAID'
      ? 'Khoản học phí của sinh viên này đã được thanh toán. Không thể thực hiện thanh toán.'
      : 'Khoản học phí đang được xử lý bởi giao dịch khác.';
    $('submit-payment-btn').disabled = true;
    return;
  }
  if (!hasBalance) {
    $('payment-eligibility-msg').textContent = 'Số dư khả dụng nhỏ hơn số tiền học phí cần thanh toán. Giao dịch không thể thực hiện.';
    $('submit-payment-btn').disabled = true;
    return;
  }
  if (!termsOk) {
    $('payment-eligibility-msg').textContent = 'Học phí hợp lệ và đủ số dư. Vui lòng tích chọn đồng ý điều khoản hệ thống để mở nút Xác nhận giao dịch.';
    $('submit-payment-btn').disabled = true;
    return;
  }
  $('payment-eligibility-msg').textContent = 'Thông tin hợp lệ. Nhấn "Xác nhận giao dịch" để nhận mã OTP qua email.';
  $('submit-payment-btn').disabled = false;
}

async function lookupTuition(rawMssv) {
  const sid = (rawMssv || '').trim().toUpperCase();
  if (!/^[A-Za-z0-9]{3,30}$/.test(sid)) {
    $('tuition-error').textContent = 'Mã số sinh viên (MSSV) không hợp lệ.';
    return;
  }
  $('lookup-tuition-btn').disabled = true;
  $('tuition-error').textContent = '';
  try {
    const data = await api('/tuition/' + encodeURIComponent(sid));
    if (data.payer && profile) {
      profile.balance = data.payer.balance;
      $('balance').textContent = money(profile.balance);
    }
    currentTuition = data;
    $('tuition-student-id').textContent = data.student_id;
    $('tuition-student-name').textContent = data.student_name;
    $('tuition-meta').textContent = [data.faculty, data.major, data.semester].filter(Boolean).join(' · ');
    const badge = $('tuition-status-badge');
    badge.textContent = tuitionStatuses[data.status] || data.status;
    badge.className = 'badge' + (data.status === 'PAID' ? ' success' : data.status === 'UNPAID' ? '' : ' failure');
    $('tuition-amount').textContent = money(data.amount);
    $('tuition-details').hidden = false;
    const idx = studentList.findIndex((item) => item.student_id === data.student_id);
    if (idx !== -1) {
      studentList[idx] = { ...studentList[idx], ...data };
      renderStudentList();
    } else {
      highlightSelectedStudent(data.student_id);
    }
    updatePaymentEligibility();
  } catch (error) {
    currentTuition = null;
    $('tuition-details').hidden = true;
    highlightSelectedStudent('');
    $('tuition-error').textContent = error.status === 503
      ? 'Chưa kết nối được Payment/Tuition Service. Hãy kiểm tra các dịch vụ đang chạy.'
      : error.message;
    updatePaymentEligibility();
  } finally {
    $('lookup-tuition-btn').disabled = false;
  }
}

function resetSession() {
  token = '';
  profile = null;
  payment = null;
  studentList = [];
  clearInterval(timer);
  clearTuitionState();
  sessionStorage.removeItem('ibanking-token');
  $('app-view').hidden = true;
  $('login-view').hidden = false;
  $('password').value = '';
  $('otp').value = '';
  $('student-id-input').value = '';
  $('terms-accepted').checked = false;
  $('payment-summary').hidden = true;
  $('history-body').replaceChildren();
  $('student-list-body').replaceChildren();
}

async function api(path, method = 'GET', body, extraHeaders = {}) {
  let response;
  try {
    response = await fetch('/api' + path, {
      method,
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: 'Bearer ' + token } : {}), ...extraHeaders },
      ...(body ? { body: JSON.stringify(body) } : {})
    });
  } catch {
    throw new Error('Không kết nối được máy chủ. Kiểm tra các dịch vụ đang chạy.');
  }
  let data;
  try { data = await response.json(); } catch { throw new Error('Máy chủ trả dữ liệu không hợp lệ.'); }
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      resetSession();
      notice('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.');
    }
    const error = new Error(data.error?.message || 'Không thể xử lý yêu cầu.');
    error.status = response.status;
    error.code = data.error?.code;
    throw error;
  }
  return data;
}

async function refreshAccount() {
  profile = await api('/auth/me');
  $('greeting').textContent = profile.full_name;
  $('balance').textContent = money(profile.balance);
  $('card-name').textContent = profile.full_name;
  $('full-name').textContent = profile.full_name;
  $('phone').textContent = profile.phone;
  $('email').textContent = profile.email;
  $('payer-full-name').textContent = profile.full_name;
  $('payer-phone').textContent = profile.phone;
  $('payer-email').textContent = profile.email;
  updatePaymentEligibility();
  await loadStudentList();
}

function selectTab(name) {
  for (const button of document.querySelectorAll('[data-tab]')) {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
  for (const tab of ['account', 'history', 'otp']) $(tab + '-tab').hidden = tab !== name;
  if (name === 'history') loadHistory();
  if (name === 'account' && token) loadStudentList();
}

async function loadHistory() {
  $('history-state').textContent = 'Đang tải lịch sử giao dịch…';
  $('history-table').hidden = true;
  $('history-body').replaceChildren();
  try {
    const data = await api('/transactions');
    if (!Array.isArray(data.items)) throw new Error('Dữ liệu lịch sử chưa đúng hợp đồng API. Cần trường items là một danh sách.');
    $('history-state').textContent = data.items.length ? '' : 'Bạn chưa có giao dịch nào.';
    for (const row of data.items) {
      const tr = document.createElement('tr');
      for (const value of [row.id, row.student_id, money(row.amount), statuses[row.status] || row.status, new Date((row.completed_at || row.created_at) * 1000).toLocaleString('vi-VN')]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      const cell = tr.children[3];
      const badge = document.createElement('span');
      badge.className = 'badge' + (row.status === 'SUCCESS' ? ' success' : ['FAILED', 'EXPIRED', 'CANCELLED'].includes(row.status) ? ' failure' : '');
      badge.textContent = cell.textContent;
      cell.replaceChildren(badge);
      $('history-body').append(tr);
    }
    $('history-table').hidden = data.items.length === 0;
  } catch (error) {
    $('history-state').textContent = error.status === 503 ? 'Chưa kết nối được Payment Service của thành viên 2. Hãy khởi chạy dịch vụ để xem lịch sử thật.' : error.message;
  }
}

function countdown() {
  if (!payment) return;
  const seconds = Math.max(0, Math.ceil(payment.expires_at - Date.now() / 1000));
  $('countdown').textContent = seconds > 0 ? `OTP còn hiệu lực ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : 'OTP đã hết hạn. Vui lòng tạo giao dịch mới từ màn hình thanh toán.';
  $('confirm-otp').disabled = seconds <= 0;
  if (seconds <= 0) clearInterval(timer);
}

async function openOtp(transactionId) {
  if (!/^[A-Za-z0-9_-]{3,100}$/.test(transactionId)) throw new Error('Mã giao dịch không hợp lệ.');
  clearInterval(timer);
  payment = null;
  $('payment-summary').hidden = true;
  $('otp-error').textContent = '';
  $('payment-result').hidden = true;
  $('otp').value = '';
  selectTab('otp');
  $('transaction-id').value = transactionId;
  const result = await api('/payments/' + encodeURIComponent(transactionId));
  if (result.id !== transactionId || result.user_id !== profile.id) throw new Error('Giao dịch không thuộc tài khoản đang đăng nhập.');
  if (result.status !== 'PENDING') {
    $('payment-result').hidden = false;
    $('payment-result').textContent = 'Trạng thái giao dịch: ' + (statuses[result.status] || result.status);
    return;
  }
  if (!Number.isFinite(result.expires_at) || !Number.isInteger(result.amount) || result.amount <= 0) throw new Error('Thông tin giao dịch chưa đúng hợp đồng API.');
  payment = result;
  $('otp-payer').textContent = profile.full_name;
  $('otp-student').textContent = result.student_name;
  $('otp-student-id').textContent = result.student_id;
  $('otp-amount').textContent = money(result.amount);
  $('payment-summary').hidden = false;
  $('otp-form').hidden = false;
  countdown();
  timer = setInterval(countdown, 1000);
  $('otp').focus();
}

window.NhatLinh = Object.freeze({ openOtp });
window.addEventListener('ibanking:payment-created', (event) => {
  openOtp(event.detail?.transaction_id || '').catch((error) => { $('otp-error').textContent = error.message; });
});

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').textContent = '';
  $('login-submit').disabled = true;
  try {
    const response = await api('/auth/login', 'POST', { username: $('username').value.trim(), password: $('password').value });
    token = response.access_token;
    sessionStorage.setItem('ibanking-token', token);
    await refreshAccount();
    $('password').value = '';
    $('login-view').hidden = true;
    $('app-view').hidden = false;
    selectTab('account');
    notice('');
  } catch (error) {
    resetSession();
    $('login-error').textContent = error.message;
  } finally { $('login-submit').disabled = false; }
});

$('toggle-password').addEventListener('click', () => {
  const show = $('password').type === 'password';
  $('password').type = show ? 'text' : 'password';
  $('toggle-password').textContent = show ? 'Ẩn' : 'Hiện';
  $('toggle-password').setAttribute('aria-label', show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu');
});
$('logout').addEventListener('click', async () => {
  let message = 'Đã đăng xuất.';
  try { await api('/auth/logout', 'POST', {}); } catch { message = 'Đã đóng phiên trên trình duyệt. Nếu mất kết nối, phiên máy chủ tự hết hạn sau tối đa 1 giờ.'; }
  resetSession();
  notice(message);
});
for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => selectTab(button.dataset.tab));
$('refresh-account').addEventListener('click', () => refreshAccount().then(() => notice('Đã cập nhật số dư và danh sách sinh viên.')).catch((error) => notice(error.message)));
$('refresh-students-btn').addEventListener('click', () => loadStudentList().then(() => notice('Đã cập nhật danh sách sinh viên.')).catch((error) => notice(error.message)));
$('refresh-history').addEventListener('click', loadHistory);
$('go-otp').addEventListener('click', () => selectTab('otp'));

$('tuition-lookup-form').addEventListener('submit', (event) => {
  event.preventDefault();
  clearTimeout(lookupDebounce);
  lookupTuition($('student-id-input').value);
});

$('student-id-input').addEventListener('input', () => {
  const val = $('student-id-input').value.trim();
  clearTuitionState();
  highlightSelectedStudent(val);
  if (/^[A-Za-z0-9]{6,30}$/.test(val)) {
    lookupDebounce = setTimeout(() => lookupTuition(val), 300);
  }
});

$('terms-accepted').addEventListener('change', updatePaymentEligibility);

$('create-payment-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!currentTuition || currentTuition.status !== 'UNPAID' || !$('terms-accepted').checked) return;
  $('submit-payment-btn').disabled = true;
  $('tuition-error').textContent = '';
  try {
    const created = await api('/payments', 'POST', {
      student_id: currentTuition.student_id,
      amount: currentTuition.amount,
      terms_accepted: true
    });
    notice(`Đã tạo giao dịch ${created.id}. Mã OTP đã được gửi tới email ${profile.email}.`);
    window.dispatchEvent(new CustomEvent('ibanking:payment-created', { detail: { transaction_id: created.id } }));
  } catch (error) {
    $('tuition-error').textContent = error.message;
    if (currentTuition) lookupTuition(currentTuition.student_id);
  } finally {
    updatePaymentEligibility();
  }
});

$('load-payment-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('load-payment').disabled = true;
  try { await openOtp($('transaction-id').value.trim()); }
  catch (error) { $('otp-error').textContent = error.status === 503 ? 'Chưa kết nối Payment Service. Cần giao dịch thật từ phần của thành viên 2 để nhập OTP.' : error.message; }
  finally { $('load-payment').disabled = false; }
});
$('transaction-id').addEventListener('input', () => {
  payment = null;
  clearInterval(timer);
  $('payment-summary').hidden = true;
  $('payment-result').hidden = true;
});
$('otp').addEventListener('input', () => { $('otp').value = $('otp').value.replace(/[^0-9]/g, '').slice(0, 6); });
$('otp-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!payment || payment.expires_at * 1000 <= Date.now()) return;
  const current = payment;
  $('confirm-otp').disabled = true;
  clearInterval(timer);
  $('otp-error').textContent = '';
  try {
    const result = await api('/payments/' + encodeURIComponent(current.id) + '/confirm', 'POST', { otp: $('otp').value });
    $('otp').value = '';
    $('payment-result').hidden = false;
    $('payment-result').textContent = result.status === 'SUCCESS' ? `Thanh toán thành công. Mã giao dịch: ${result.id}. Email xác nhận sẽ được gửi đến người nộp.` : `Trạng thái: ${statuses[result.status] || result.status}. Kiểm tra lịch sử trước khi thực hiện lại.`;
    $('otp-form').hidden = true;
    $('countdown').textContent = '';
    payment = null;
    try {
      await refreshAccount();
      if (currentTuition && currentTuition.student_id === current.student_id) {
        await lookupTuition(current.student_id);
      }
    } catch (error) { notice(error.message); }
  } catch (error) {
    $('otp-error').textContent = error.message;
    if (['OTP_EXPIRED', 'OTP_LOCKED', 'OTP_ALREADY_USED', 'INSUFFICIENT_BALANCE', 'TUITION_ALREADY_PAID'].includes(error.code)) {
      $('otp-form').hidden = true;
      $('countdown').textContent = 'Kiểm tra lại trạng thái giao dịch từ lịch sử.';
      payment = null;
      loadStudentList().catch(() => {});
      if (currentTuition && currentTuition.student_id === current.student_id) {
        lookupTuition(current.student_id).catch(() => {});
      }
    } else if (payment) {
      countdown();
      timer = setInterval(countdown, 1000);
    }
  }
});

if (token) refreshAccount().then(() => {
  $('login-view').hidden = true;
  $('app-view').hidden = false;
  selectTab('account');
  const transactionId = new URLSearchParams(location.search).get('transaction_id');
  if (transactionId) openOtp(transactionId).catch((error) => { $('otp-error').textContent = error.message; });
}).catch((error) => { resetSession(); notice(error.message); });
