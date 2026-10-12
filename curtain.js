/* Pre-launch curtain. Until the June 1 2027 relaunch every page opens on the Reborn poster with a key prompt; the
   right key unlocks the site in this browser (remembered), and a tester link ?key=... unlocks in one click.
   Loaded synchronously as the first thing in <head> so nothing behind it paints first.
   Only a salted SHA-256 of the key lives here. This keeps the public out; it is not access control for data,
   which stays public on the CDN as before. To lift the curtain at launch: set OPEN = true (or remove the tag). */
(function () {
  "use strict";
  var OPEN = false;
  var SALT = "tat-reborn-2027:";
  var HASH = "b2470f6a7edf430b66e3d09402b1f118837b3ebaad7ccc6f861f8efb68a5d4db";
  var STORE = "tat-reborn-key";
  if (OPEN) return;
  var d = document, de = d.documentElement;
  function get() { try { return localStorage.getItem(STORE); } catch (e) { return null; } }
  function put(h) { try { localStorage.setItem(STORE, h); } catch (e) {} }
  if (get() === HASH) return;
  de.classList.add("tat-locked");
  var css = d.createElement("style");
  css.textContent = "html.tat-locked body>*:not(#tatCurtain){display:none!important}html.tat-locked,html.tat-locked body{overflow:hidden!important;background:#05070d!important}" +
    "#tatCurtain{position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:20px 16px;" +
    "background:#05070d;font-family:Metropolis,Arial,sans-serif;color:#fff;overflow:hidden}" +
    "#tatCurtain::before{content:'';position:absolute;inset:-140px;background:url(/assets/reborn.jpg) center/cover;filter:blur(40px) brightness(.45);pointer-events:none}" +
    "#tatCurtain img{position:relative;display:block;max-width:min(100%,900px);max-height:calc(100vh - 150px);object-fit:contain;border-radius:3px;box-shadow:0 18px 50px rgba(0,0,0,.6)}" +
    "#tatCurtain form{position:relative;display:flex;gap:8px;width:min(420px,100%)}" +
"#tatCurtain input{flex:1;min-width:0;font:inherit;font-size:16px;font-weight:700;letter-spacing:.06em;color:#fff;background:rgba(10,18,40,.82);border:1px solid rgba(214,227,255,.4);border-radius:3px;padding:12px 14px;outline:none}" +
    "#tatCurtain input:focus{border-color:#e8b53a}" +
    "#tatCurtain button{font:inherit;font-size:13px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#06101f;background:#e8b53a;border:0;border-radius:3px;padding:0 18px;cursor:pointer}" +
    "#tatCurtain .msg{position:relative;min-height:16px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#d6e3ff}" +
    "#tatCurtain .msg.bad{color:#ff8a8a}";
  (d.head || de).appendChild(css);
  function sha(s) {
    var b = new TextEncoder().encode(SALT + s);
    return crypto.subtle.digest("SHA-256", b).then(function (h) {
      return Array.prototype.map.call(new Uint8Array(h), function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    });
  }
  function open() { put(HASH); de.classList.remove("tat-locked"); var c = d.getElementById("tatCurtain"); if (c) c.remove(); css.remove(); }
  /* tester link: ?key=... unlocks, then the key is stripped from the address bar */
  var q = new URLSearchParams(location.search), k = q.get("key");
  if (k) {
    q.delete("key"); var clean = location.pathname + (q.toString() ? "?" + q : "") + location.hash;
    try { history.replaceState(null, "", clean); } catch (e) {}
    sha(k.trim()).then(function (h) { if (h === HASH) open(); });
  }
  function mount() {
    if (!de.classList.contains("tat-locked") || d.getElementById("tatCurtain")) return;
    var c = d.createElement("div"); c.id = "tatCurtain"; c.setAttribute("role", "dialog"); c.setAttribute("aria-label", "Triple-A-Tropics, relaunching June 1 2027");
    c.innerHTML = '<img src="/assets/reborn.jpg" alt="Triple-A-Tropics Reborn: full site overhaul, June 1 2027"><form autocomplete="off"><input type="password" name="k" placeholder="Tester key" aria-label="Tester key" autofocus><button type="submit">Enter</button></form><div class="msg"></div>';
    d.body.appendChild(c);
    var f = c.querySelector("form"), i = c.querySelector("input"), m = c.querySelector(".msg");
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      sha(i.value.trim()).then(function (h) {
        if (h === HASH) open();
        else { m.textContent = "That key isn't right"; m.className = "msg bad"; i.select(); }
      });
    });
    i.focus();
  }
  if (d.body) mount(); else d.addEventListener("DOMContentLoaded", mount);
})();
