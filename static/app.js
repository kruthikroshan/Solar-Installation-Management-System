// Signature pad, camera scanner, confirm prompts and click-to-fill demo logins.
(function () {
  var c = document.getElementById('sig');
  if (c) {
    var ctx = c.getContext('2d'), drawing = false, drawn = false, hid = document.getElementById('signature');
    function fit() { var r = c.getBoundingClientRect(); c.width = r.width; c.height = r.height; ctx.lineWidth = 2.2; ctx.lineCap = 'round'; ctx.strokeStyle = '#1b2733'; }
    fit();
    function pos(e) { var r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    c.addEventListener('pointerdown', function (e) { drawing = true; var p = pos(e); ctx.beginPath(); ctx.moveTo(p[0], p[1]); c.setPointerCapture(e.pointerId); });
    c.addEventListener('pointermove', function (e) { if (!drawing) return; var p = pos(e); ctx.lineTo(p[0], p[1]); ctx.stroke(); drawn = true; });
    c.addEventListener('pointerup', function () { drawing = false; if (drawn) hid.value = c.toDataURL('image/png'); });
    var clr = document.getElementById('sigClear');
    if (clr) clr.onclick = function () { ctx.clearRect(0, 0, c.width, c.height); hid.value = ''; drawn = false; };
  }
  var sb = document.getElementById('scanBtn'), ta = document.getElementById(sb && sb.dataset.target);
  if (sb && !('BarcodeDetector' in window)) sb.style.display = 'none';
  if (sb && 'BarcodeDetector' in window) sb.onclick = async function () {
    var v = document.getElementById('scanVideo'), det = new BarcodeDetector({ formats: ['qr_code', 'code_128'] });
    var stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    v.srcObject = stream; v.style.display = 'block'; await v.play();
    var seen = {}, t = setInterval(async function () {
      var codes = await det.detect(v);
      codes.forEach(function (b) { if (!seen[b.rawValue]) { seen[b.rawValue] = 1; ta.value += (ta.value && !ta.value.endsWith('\n') ? '\n' : '') + b.rawValue + '\n'; if (navigator.vibrate) navigator.vibrate(60); } });
    }, 400);
    sb.textContent = 'Stop camera'; sb.onclick = function () { clearInterval(t); stream.getTracks().forEach(function (x) { x.stop(); }); v.style.display = 'none'; sb.textContent = 'Scan with camera'; };
  };
  document.querySelectorAll('[data-confirm]').forEach(function (f) { f.addEventListener('submit', function (e) { if (!confirm(f.dataset.confirm)) e.preventDefault(); }); });
  document.querySelectorAll('.demo tr[data-email]').forEach(function (r) { r.onclick = function () { document.getElementById('email').value = r.dataset.email; document.getElementById('password').focus(); }; });
})();
