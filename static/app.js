const $ = (id) => document.getElementById(id);

let clients = [];
let readyClientIds = new Set();

function log(msg) {
  const box = $("logBox");
  const ts = new Date().toLocaleTimeString();
  box.textContent = `[${ts}] ${msg}\n` + box.textContent;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem("gnb_admin_token");
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...opts, headers: { ...headers, ...opts.headers } });
  if (path.startsWith("/api/download")) {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || "下载失败");
    }
    return res;
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function downloadZip(path, filename) {
  const headers = {};
  const token = localStorage.getItem("gnb_admin_token");
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "下载失败");
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function downloadNode(nodeid) {
  await downloadZip(`/api/download/${nodeid}`, `gnb-node-${nodeid}.zip`);
  log(`已下载节点 ${nodeid} 配置包`);
}

const DEFAULT_BIN_DIR = "/data2/opengnb-1.6.5/bin";
const GNB_DEFAULT_DETECT_INTERVAL = "5000,367";
const GNB_DEFAULT_WORKER_QUEUE = "4095";

const GNB_ADV_DEFAULTS = {
  safe_index: "off",
  crypto_key_update_interval: "",
  multi_forward_type: "",
  memory: "",
  zip: "",
  zip_level: "",
  detect_interval: "",
  node_worker_queue: "",
  index_worker_queue: "",
  index_service_worker_queue: "",
  packet_filter_worker_queue: "",
  pf_worker: "0",
  pf_route_bits: "",
};

const CLIENT_ADV_DEFAULTS = {
  platform: "linux",
  if_drv: "wintun",
  multi_socket: "on",
  extra_listens: [],
  node_detect_worker: "on",
  set_fwdu0: "on",
  direct_forwarding: "on",
  es_argv: "--upnp",
  discover_in_lan: false,
  unified_forwarding: "auto",
  ip_stack: "both",
  mtu: "",
  ...GNB_ADV_DEFAULTS,
};

function onoffSelect(id, val, fallback = "on") {
  const el = $(id);
  if (el) el.value = val === "off" ? "off" : val || fallback;
}

function isIndexForwardEnabled() {
  return $("idxEnableForward").value === "1";
}

function syncIndexForwardMode() {
  const on = isIndexForwardEnabled();
  ["idxSetFwdu0", "idxDirectForwarding", "idxUnifiedForwarding"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.disabled = !on;
    const label = el.closest("label");
    if (label) label.classList.toggle("disabled-field", !on);
  });
  const hint = $("idxModeHint");
  if (hint) {
    hint.textContent = on
      ? "Index + Forward：address.conf 为 if|nodeid|...；内网节点经此节点做 Forward 中继"
      : "仅 Index：address.conf 为 i|nodeid|...；set-fwdu0 / direct-forwarding 自动关闭，不提供 Forward 中继";
  }
  if (!on) {
    onoffSelect("idxSetFwdu0", "off", "off");
    onoffSelect("idxDirectForwarding", "off", "off");
  }
}

function collectGnbAdvanced(prefix) {
  const pfWorker = $(`${prefix}PfWorker`).value.trim();
  return {
    safe_index: $(`${prefix}SafeIndex`).value,
    crypto_key_update_interval: $(`${prefix}CryptoKeyInterval`).value,
    multi_forward_type: $(`${prefix}MultiForwardType`).value,
    memory: $(`${prefix}Memory`).value,
    zip: $(`${prefix}Zip`).value,
    zip_level: $(`${prefix}ZipLevel`).value.trim(),
    detect_interval: $(`${prefix}DetectInterval`).value.trim(),
    node_worker_queue: $(`${prefix}NodeWorkerQueue`).value.trim(),
    index_worker_queue: $(`${prefix}IndexWorkerQueue`)?.value.trim() || "",
    index_service_worker_queue: $(`${prefix}IndexServiceWorkerQueue`)?.value.trim() || "",
    packet_filter_worker_queue: $(`${prefix}PacketFilterWorkerQueue`).value.trim(),
    pf_worker: pfWorker === "" ? "0" : pfWorker,
    pf_route_bits: $(`${prefix}PfRouteBits`).value.trim(),
  };
}

function fillGnbAdvanced(prefix, data) {
  const d = { ...GNB_ADV_DEFAULTS, ...data };
  onoffSelect(`${prefix}SafeIndex`, d.safe_index, "off");
  $(`${prefix}CryptoKeyInterval`).value = d.crypto_key_update_interval || "";
  $(`${prefix}MultiForwardType`).value = d.multi_forward_type || "";
  $(`${prefix}Memory`).value = d.memory || "";
  $(`${prefix}Zip`).value = d.zip || "";
  $(`${prefix}ZipLevel`).value = d.zip_level || "";
  $(`${prefix}DetectInterval`).value = d.detect_interval || "";
  $(`${prefix}NodeWorkerQueue`).value = d.node_worker_queue || "";
  if ($(`${prefix}IndexWorkerQueue`)) {
    $(`${prefix}IndexWorkerQueue`).value = d.index_worker_queue || "";
  }
  if ($(`${prefix}IndexServiceWorkerQueue`)) {
    $(`${prefix}IndexServiceWorkerQueue`).value = d.index_service_worker_queue || "";
  }
  $(`${prefix}PacketFilterWorkerQueue`).value = d.packet_filter_worker_queue || "";
  $(`${prefix}PfWorker`).value = d.pf_worker ?? "0";
  $(`${prefix}PfRouteBits`).value = d.pf_route_bits || "";
}

function syncIndexDetectIntervalField() {
  const on = $("idxNodeDetectWorker").value === "on";
  const el = $("idxDetectInterval");
  const label = $("idxDetectIntervalLabel");
  if (el) el.disabled = !on;
  if (label) label.classList.toggle("disabled-field", !on);
}

function syncGnbLogFields() {
  const on = $("idxGnbLogEnabled").value === "1";
  const el = $("idxGnbLogPath");
  const label = $("idxGnbLogPathLabel");
  if (el) el.disabled = !on;
  if (label) label.classList.toggle("disabled-field", !on);
}

function fillIndexLogFields(idx) {
  $("idxGnbLogEnabled").value = idx.gnb_log_enabled ? "1" : "0";
  $("idxGnbLogPath").value = idx.gnb_log_path || "logs/gnb-{nodeid}.log";
  syncGnbLogFields();
}

function collectIndexAdvanced() {
  return {
    index_worker: $("idxIndexWorker").value,
    index_service_worker: $("idxIndexServiceWorker").value,
    node_detect_worker: $("idxNodeDetectWorker").value,
    set_fwdu0: $("idxSetFwdu0").value,
    direct_forwarding: $("idxDirectForwarding").value,
    unified_forwarding: $("idxUnifiedForwarding").value,
    ip_stack: $("idxIpStack").value,
    mtu: $("idxMtu").value.trim(),
    es_argv: $("idxEsArgv").value.trim(),
    gnb_log_enabled: $("idxGnbLogEnabled").value === "1",
    gnb_log_path: $("idxGnbLogPath").value.trim() || "logs/gnb-{nodeid}.log",
    ...collectGnbAdvanced("idx"),
  };
}

function fillIndexAdvanced(idx) {
  onoffSelect("idxIndexWorker", idx.index_worker, "on");
  onoffSelect("idxIndexServiceWorker", idx.index_service_worker, "on");
  onoffSelect("idxNodeDetectWorker", idx.node_detect_worker, "off");
  onoffSelect("idxSetFwdu0", idx.set_fwdu0, "on");
  onoffSelect("idxDirectForwarding", idx.direct_forwarding, "on");
  $("idxUnifiedForwarding").value = idx.unified_forwarding || "";
  $("idxIpStack").value = idx.ip_stack || "both";
  $("idxMtu").value = idx.mtu || "";
  $("idxEsArgv").value = idx.es_argv || "";
  fillGnbAdvanced("idx", idx);
  fillIndexLogFields(idx);
  syncIndexDetectIntervalField();
}

function parseExtraListensStr(s) {
  if (!s || !String(s).trim()) return [];
  return String(s)
    .split(",")
    .map((p) => parseInt(p.trim(), 10))
    .filter((n) => !Number.isNaN(n));
}

function formatExtraListens(arr) {
  if (!arr || !arr.length) return "";
  return arr.join(",");
}

function syncClientPlatform(clientIdx) {
  const c = clients[clientIdx];
  const isWin = (c.platform || "linux") === "windows";
  const label = document.querySelector(`[data-if-drv-label="${clientIdx}"]`);
  if (label) {
    label.classList.toggle("disabled-field", !isWin);
    const sel = label.querySelector("select");
    if (sel) sel.disabled = !isWin;
  }
}

function renderClients() {
  const list = $("clientList");
  list.innerHTML = "";
  clients.forEach((c, i) => {
    const ready = readyClientIds.has(c.nodeid);
    const div = document.createElement("div");
    div.className = "client-block";
    div.innerHTML = `
      <div class="client-head">
        <strong>内网节点 #${i + 1}（nodeid ${c.nodeid}）</strong>
        <div class="client-actions">
          <button class="btn small primary" data-dl="${c.nodeid}" ${ready ? "" : "disabled"} title="${ready ? "下载本节点配置 ZIP" : "请先生成全部配置"}">下载配置</button>
          <button class="btn small danger" data-remove="${i}">删除</button>
        </div>
      </div>
      <div class="grid">
        <label>Node ID<input type="number" data-i="${i}" data-f="nodeid" value="${c.nodeid}"></label>
        <label>监听端口<input type="number" data-i="${i}" data-f="listen" value="${c.listen}"></label>
        <label>TUN IP<input type="text" data-i="${i}" data-f="tun_ip" value="${c.tun_ip}"></label>
        <label>multi-socket
          <select data-i="${i}" data-f="multi_socket">
            <option value="on" ${c.multi_socket !== "off" ? "selected" : ""}>on</option>
            <option value="off" ${c.multi_socket === "off" ? "selected" : ""}>off</option>
          </select>
        </label>
        <label>额外固定端口<input type="text" data-i="${i}" data-f="extra_listens" value="${formatExtraListens(c.extra_listens)}" placeholder="留空则仅用主端口/multi-socket"></label>
        <label>运行平台
          <select data-i="${i}" data-f="platform">
            <option value="linux" ${(c.platform || "linux") === "linux" ? "selected" : ""}>Linux / OpenWRT</option>
            <option value="windows" ${c.platform === "windows" ? "selected" : ""}>Windows</option>
          </select>
        </label>
        <label class="client-if-drv-label" data-if-drv-label="${i}">
          TUN 驱动（Windows）
          <select data-i="${i}" data-f="if_drv" ${(c.platform || "linux") !== "windows" ? "disabled" : ""}>
            <option value="wintun" ${(c.if_drv || "wintun") === "wintun" ? "selected" : ""}>wintun（推荐）</option>
            <option value="tap-windows" ${c.if_drv === "tap-windows" ? "selected" : ""}>tap-windows</option>
          </select>
        </label>
      </div>
      <div class="grid client-advanced">
        <label>es-argv（gnb_es）
          <input type="text" data-i="${i}" data-f="es_argv" value="${c.es_argv || "--upnp"}" placeholder="--upnp">
        </label>
        <label>LAN 发现（-L）
          <select data-i="${i}" data-f="discover_in_lan">
            <option value="0" ${!c.discover_in_lan ? "selected" : ""}>关</option>
            <option value="1" ${c.discover_in_lan ? "selected" : ""}>开</option>
          </select>
        </label>
        <label>unified-forwarding
          <select data-i="${i}" data-f="unified_forwarding">
            <option value="off" ${c.unified_forwarding === "off" ? "selected" : ""}>off</option>
            <option value="auto" ${(!c.unified_forwarding || c.unified_forwarding === "auto") ? "selected" : ""}>auto</option>
            <option value="super" ${c.unified_forwarding === "super" ? "selected" : ""}>super</option>
            <option value="hyper" ${c.unified_forwarding === "hyper" ? "selected" : ""}>hyper</option>
          </select>
        </label>
        <label>node-detect-worker
          <select data-i="${i}" data-f="node_detect_worker">
            <option value="on" ${c.node_detect_worker !== "off" ? "selected" : ""}>on</option>
            <option value="off" ${c.node_detect_worker === "off" ? "selected" : ""}>off</option>
          </select>
        </label>
        <label>IP 协议栈
          <select data-i="${i}" data-f="ip_stack">
            <option value="both" ${(c.ip_stack || "both") === "both" ? "selected" : ""}>IPv4+IPv6</option>
            <option value="ipv4" ${c.ip_stack === "ipv4" ? "selected" : ""}>仅 IPv4</option>
            <option value="ipv6" ${c.ip_stack === "ipv6" ? "selected" : ""}>仅 IPv6</option>
          </select>
        </label>
        <label>MTU<input type="number" data-i="${i}" data-f="mtu" value="${c.mtu || ""}" placeholder="1280"></label>
      </div>
      <details class="client-adv-details">
        <summary>性能 / 安全（gnb 高级选项）</summary>
        <div class="grid client-advanced">
          <label>safe-index
            <select data-i="${i}" data-f="safe_index">
              <option value="off" ${(c.safe_index || "off") === "off" ? "selected" : ""}>off</option>
              <option value="on" ${c.safe_index === "on" ? "selected" : ""}>on</option>
            </select>
          </label>
          <label>crypto-key-update-interval
            <select data-i="${i}" data-f="crypto_key_update_interval">
              <option value="" ${!c.crypto_key_update_interval ? "selected" : ""}>不设置</option>
              <option value="hour" ${c.crypto_key_update_interval === "hour" ? "selected" : ""}>hour</option>
              <option value="minute" ${c.crypto_key_update_interval === "minute" ? "selected" : ""}>minute</option>
            </select>
          </label>
          <label>multi-forward-type
            <select data-i="${i}" data-f="multi_forward_type">
              <option value="" ${!c.multi_forward_type ? "selected" : ""}>默认</option>
              <option value="simple-load-balance" ${c.multi_forward_type === "simple-load-balance" ? "selected" : ""}>simple-load-balance</option>
            </select>
          </label>
          <label>memory
            <select data-i="${i}" data-f="memory">
              <option value="" ${!c.memory ? "selected" : ""}>默认 tiny</option>
              <option value="small" ${c.memory === "small" ? "selected" : ""}>small</option>
              <option value="large" ${c.memory === "large" ? "selected" : ""}>large</option>
              <option value="huge" ${c.memory === "huge" ? "selected" : ""}>huge</option>
            </select>
          </label>
          <label>zip
            <select data-i="${i}" data-f="zip">
              <option value="" ${!c.zip ? "selected" : ""}>默认 auto</option>
              <option value="force" ${c.zip === "force" ? "selected" : ""}>force</option>
            </select>
          </label>
          <label>zip-level<input type="number" min="0" max="9" data-i="${i}" data-f="zip_level" value="${c.zip_level || ""}" placeholder="0~9"></label>
          <label class="client-detect-label" data-detect-label="${i}">detect-interval
            <input type="text" data-i="${i}" data-f="detect_interval" value="${c.detect_interval || ""}" placeholder="${GNB_DEFAULT_DETECT_INTERVAL}" ${c.node_detect_worker === "off" ? "disabled" : ""}>
          </label>
          <label>node-worker-queue<input type="number" data-i="${i}" data-f="node_worker_queue" value="${c.node_worker_queue || ""}" placeholder="${GNB_DEFAULT_WORKER_QUEUE}"></label>
          <label>packet-filter-worker-queue<input type="number" data-i="${i}" data-f="packet_filter_worker_queue" value="${c.packet_filter_worker_queue || ""}" placeholder="${GNB_DEFAULT_WORKER_QUEUE}"></label>
          <label>pf-worker<input type="number" min="0" max="128" data-i="${i}" data-f="pf_worker" value="${c.pf_worker ?? "0"}"></label>
          <label>pf-route-bits<input type="text" data-i="${i}" data-f="pf_route_bits" value="${c.pf_route_bits || ""}" placeholder="0x1"></label>
        </div>
        <p class="field-hint">detect-interval 在 node-detect-worker=on 时生效；pf-route-bits=0x1 可经 Forward 中继与未交换密钥的节点通讯</p>
      </details>
      <p class="route-hint" data-route-hint="${i}">LAN 路由（${(c.platform || "linux") === "windows" ? "Windows 需自行配置系统路由" : "if_up_linux.sh"}）<span>灰色为示例，填写后才会保存</span></p>
      <div class="routes" data-routes="${i}"></div>
      <button class="btn small" data-add-route="${i}" style="margin-top:8px">+ 添加路由</button>
    `;
    list.appendChild(div);
    renderRoutes(i);
    syncClientPlatform(i);
  });

  list.querySelectorAll("[data-dl]").forEach((btn) => {
    btn.onclick = async () => {
      try {
        await downloadNode(+btn.dataset.dl);
      } catch (e) {
        log("下载失败: " + e.message);
      }
    };
  });
  list.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.onclick = () => {
      clients.splice(+btn.dataset.remove, 1);
      renderClients();
    };
  });
  list.querySelectorAll("[data-add-route]").forEach((btn) => {
    btn.onclick = () => {
      const ci = +btn.dataset.addRoute;
      if (!clients[ci].lan_routes) clients[ci].lan_routes = [];
      clients[ci].lan_routes.push({ network: "", netmask: "", via_tun_ip: "" });
      renderRoutes(ci);
    };
  });
  list.querySelectorAll("input[data-i]").forEach((inp) => {
    inp.onchange = () => {
      const i = +inp.dataset.i;
      const f = inp.dataset.f;
      if (f === "extra_listens") {
        clients[i][f] = parseExtraListensStr(inp.value);
      } else if (f === "mtu" || f === "zip_level" || f === "detect_interval" || f === "pf_route_bits"
        || f.startsWith("node_worker") || f.startsWith("packet_filter") || f === "pf_worker") {
        clients[i][f] = inp.value.trim();
      } else {
        clients[i][f] = f === "nodeid" || f === "listen" ? +inp.value : inp.value;
      }
    };
  });
  list.querySelectorAll("select[data-i]").forEach((sel) => {
    sel.onchange = () => {
      const i = +sel.dataset.i;
      const f = sel.dataset.f;
      if (f === "discover_in_lan") {
        clients[i][f] = sel.value === "1";
      } else {
        clients[i][f] = sel.value;
      }
      if (f === "node_detect_worker") {
        syncClientDetectInterval(i);
      }
      if (f === "platform") {
        if (sel.value === "windows" && !clients[i].if_drv) {
          clients[i].if_drv = "wintun";
        }
        syncClientPlatform(i);
      }
    };
  });
}

function syncClientDetectInterval(clientIdx) {
  const c = clients[clientIdx];
  const container = document.querySelector(`[data-detect-label="${clientIdx}"]`);
  if (!container) return;
  const inp = container.querySelector("input");
  const on = c.node_detect_worker !== "off";
  if (inp) inp.disabled = !on;
  container.classList.toggle("disabled-field", !on);
}

const LAN_ROUTE_EXAMPLES = {
  1002: { network: "192.168.3.0", netmask: "255.255.255.0", via_tun_ip: "10.1.0.3" },
  1003: { network: "192.168.2.0", netmask: "255.255.255.0", via_tun_ip: "10.1.0.2" },
};

function getRouteExample(nodeid) {
  return (
    LAN_ROUTE_EXAMPLES[nodeid] || {
      network: "192.168.x.0",
      netmask: "255.255.255.0",
      via_tun_ip: "10.1.0.x",
    }
  );
}

function syncRoutesFromDom() {
  clients.forEach((c, ci) => {
    const container = document.querySelector(`[data-routes="${ci}"]`);
    if (!container) return;
    const routes = [];
    container.querySelectorAll(".route-row").forEach((row) => {
      const network = row.querySelector('[data-rf="network"]')?.value.trim() || "";
      const netmask = row.querySelector('[data-rf="netmask"]')?.value.trim() || "";
      const via_tun_ip = row.querySelector('[data-rf="via_tun_ip"]')?.value.trim() || "";
      if (network && netmask && via_tun_ip) {
        routes.push({ network, netmask, via_tun_ip });
      }
    });
    c.lan_routes = routes;
  });
}

function buildRouteRow(clientIdx, route, isExample) {
  const client = clients[clientIdx];
  const example = getRouteExample(client.nodeid);
  const v = route || { network: "", netmask: "", via_tun_ip: "" };
  const cls = isExample ? "route-row route-example" : "route-row";
  const delBtn = isExample
    ? ""
    : `<button type="button" class="btn small" data-del-route="${clientIdx}">删</button>`;
  return `
    <div class="${cls}">
      <label>网段<input type="text" data-ci="${clientIdx}" data-rf="network" value="${v.network}" placeholder="${example.network}"></label>
      <label>掩码<input type="text" data-ci="${clientIdx}" data-rf="netmask" value="${v.netmask}" placeholder="${example.netmask}"></label>
      <label>via TUN IP<input type="text" data-ci="${clientIdx}" data-rf="via_tun_ip" value="${v.via_tun_ip}" placeholder="${example.via_tun_ip}"></label>
      ${delBtn}
    </div>`;
}

function renderRoutes(clientIdx) {
  const container = document.querySelector(`[data-routes="${clientIdx}"]`);
  if (!container) return;
  const client = clients[clientIdx];
  const routes = client.lan_routes || [];

  if (routes.length === 0) {
    container.innerHTML = buildRouteRow(clientIdx, null, true);
  } else {
    container.innerHTML = routes
      .map((r) => buildRouteRow(clientIdx, r, false))
      .join("");
  }

  container.querySelectorAll("[data-del-route]").forEach((btn) => {
    btn.onclick = () => {
      syncRoutesFromDom();
      const ci = +btn.dataset.delRoute;
      const row = btn.closest(".route-row");
      const rows = [...container.querySelectorAll(".route-row")];
      const idx = rows.indexOf(row);
      if (idx >= 0 && clients[ci].lan_routes[idx]) {
        clients[ci].lan_routes.splice(idx, 1);
      } else {
        clients[ci].lan_routes = [];
      }
      renderRoutes(ci);
    };
  });
}

function displayBinDir(gnbBin) {
  if (!gnbBin) return DEFAULT_BIN_DIR;
  if (gnbBin.endsWith("/gnb")) return gnbBin.slice(0, -4);
  return gnbBin;
}

function displayCryptoPath(cryptoBin, gnbBin) {
  if (!cryptoBin) return "";
  const binDir = displayBinDir(gnbBin);
  if (cryptoBin === `${binDir}/gnb_crypto`) return "";
  return cryptoBin;
}

function validateIndexPublicIp() {
  const el = $("idxPublicIp");
  const ip = el.value.trim();
  el.classList.toggle("input-invalid", !ip);
  if (!ip) {
    el.focus();
    throw new Error("请填写公网 Index 节点的「公网 IP」");
  }
  return ip;
}

function collectNetwork() {
  syncRoutesFromDom();
  const binDir = $("gnbBin").value.trim() || DEFAULT_BIN_DIR;
  const cryptoRaw = $("gnbCryptoBin").value.trim();
  return {
    index_forward: {
      enable_forward: isIndexForwardEnabled(),
      nodeid: +$("idxNodeid").value,
      listen: +$("idxListen").value,
      public_ip: $("idxPublicIp").value.trim(),
      tun_ip: $("idxTunIp").value.trim(),
      netmask: $("idxNetmask").value.trim(),
      passcode: $("idxPasscode").value.trim(),
      multi_socket: $("idxMultiSocket").value,
      extra_listens: parseExtraListensStr($("idxExtraListens").value),
      ...collectIndexAdvanced(),
    },
    clients: clients.map((c) => ({
      nodeid: c.nodeid,
      listen: c.listen,
      tun_ip: c.tun_ip,
      platform: c.platform || "linux",
      if_drv: c.platform === "windows" ? (c.if_drv || "wintun") : "wintun",
      multi_socket: c.multi_socket || "on",
      extra_listens: Array.isArray(c.extra_listens)
        ? c.extra_listens
        : parseExtraListensStr(c.extra_listens),
      node_detect_worker: c.node_detect_worker || "on",
      set_fwdu0: c.set_fwdu0 || "on",
      direct_forwarding: c.direct_forwarding || "on",
      es_argv: c.es_argv ?? "--upnp",
      discover_in_lan: !!c.discover_in_lan,
      unified_forwarding: c.unified_forwarding || "auto",
      ip_stack: c.ip_stack || "both",
      mtu: c.mtu || "",
      lan_routes: c.lan_routes || [],
      ...GNB_ADV_DEFAULTS,
      safe_index: c.safe_index || "off",
      crypto_key_update_interval: c.crypto_key_update_interval || "",
      multi_forward_type: c.multi_forward_type || "",
      memory: c.memory || "",
      zip: c.zip || "",
      zip_level: c.zip_level || "",
      detect_interval: c.detect_interval || "",
      node_worker_queue: c.node_worker_queue || "",
      packet_filter_worker_queue: c.packet_filter_worker_queue || "",
      pf_worker: c.pf_worker ?? "0",
      pf_route_bits: c.pf_route_bits || "",
    })),
    gnb_bin: binDir,
    gnb_crypto_bin: cryptoRaw,
  };
}

function fillForm(data) {
  const idx = data.index_forward;
  $("idxNodeid").value = idx.nodeid;
  $("idxListen").value = idx.listen;
  $("idxPublicIp").value = idx.public_ip || "";
  $("idxTunIp").value = idx.tun_ip;
  $("idxNetmask").value = idx.netmask;
  $("idxPasscode").value = idx.passcode || "";
  $("idxMultiSocket").value = idx.multi_socket === "on" ? "on" : "off";
  $("idxExtraListens").value = formatExtraListens(idx.extra_listens);
  $("idxEnableForward").value = idx.enable_forward !== false ? "1" : "0";
  fillIndexAdvanced(idx);
  syncIndexForwardMode();
  $("gnbBin").value = displayBinDir(data.gnb_bin);
  $("gnbCryptoBin").value = displayCryptoPath(data.gnb_crypto_bin, data.gnb_bin);
  clients = JSON.parse(JSON.stringify(data.clients || [])).map((c) => ({
    ...CLIENT_ADV_DEFAULTS,
    ...c,
    platform: c.platform || "linux",
    if_drv: c.if_drv || "wintun",
    multi_socket: c.multi_socket || "on",
    extra_listens: c.extra_listens || [],
  }));
  renderClients();
}

function setGnbTargetVersion(version) {
  if (!version) return;
  const label = `OpenGNB ${version}`;
  const badge = $("gnbTargetVersion");
  const sub = $("gnbTargetVersionSub");
  if (badge) {
    badge.textContent = label;
    badge.title = `配置项与高级选项所对齐的 OpenGNB 版本（${version}）`;
  }
  if (sub) sub.textContent = version;
  document.title = `OpenGNB 管理端 · ${version}`;
}

let gnbLogPollTimer = null;

async function refreshGnbLogs() {
  const box = $("gnbLogBox");
  const hint = $("gnbLogPathHint");
  if (!box || !hint) return;
  try {
    const r = await api("/api/gnb-logs?lines=300");
    if (!r.enabled) {
      hint.textContent = "文件日志未启用（可在公网 Index 节点区开启）";
      box.textContent = r.message || "";
      return;
    }
    const pathText = r.path || r.configured_path || "-";
    const sourceNote =
      r.source === "stdout"
        ? `（当前显示 stdout：${r.stdout_path || "data/logs/index_forward.log"}）`
        : r.source === "file"
          ? "（文件日志）"
          : "";
    hint.textContent = [
      r.message,
      r.source === "file" || r.exists
        ? `日志文件：${pathText} ${sourceNote}`.trim()
        : `目标文件：${pathText} ${sourceNote}`.trim(),
    ]
      .filter(Boolean)
      .join(" · ");
    box.textContent = r.log || "（暂无内容）";
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    hint.textContent = "读取 gnb 日志失败";
    box.textContent = e.message;
  }
}

function setGnbLogAutoRefresh(on) {
  if (gnbLogPollTimer) {
    clearInterval(gnbLogPollTimer);
    gnbLogPollTimer = null;
  }
  if (on) {
    gnbLogPollTimer = setInterval(refreshGnbLogs, 5000);
  }
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    setGnbTargetVersion(s.gnb_target_version);
    const el = $("runStatus");
    el.textContent = s.running ? "运行中" : "已停止";
    el.className = "badge " + (s.running ? "running" : "stopped");
    $("runPid").textContent =
      s.pids && s.pids.length ? s.pids.join(", ") : s.pid || "-";
    $("btnStart").disabled = s.running;
    $("btnStop").disabled = !s.running;
    readyClientIds = new Set(
      (s.clients || []).filter((c) => c.config_ready).map((c) => c.nodeid)
    );
    renderClients();
    if (readyClientIds.size > 0) {
      showDownloads([...readyClientIds]);
    }
    if (s.gnb_log?.enabled) {
      await refreshGnbLogs();
    }
  } catch (e) {
    $("runStatus").textContent = "未知";
    $("runStatus").className = "badge unknown";
    log("状态刷新失败: " + e.message);
  }
}

function showDownloads(nodeids) {
  $("downloadSection").hidden = false;
  const links = $("downloadLinks");
  links.innerHTML = "";
  nodeids.forEach((id) => {
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.textContent = `下载节点 ${id}`;
    btn.onclick = () => downloadNode(id).catch((e) => log("下载失败: " + e.message));
    links.appendChild(btn);
  });
  const all = document.createElement("button");
  all.className = "btn primary";
  all.textContent = "下载全部内网节点";
  all.onclick = () =>
    downloadZip("/api/download/all", "gnb-clients.zip")
      .then(() => log("已下载全部内网节点配置包"))
      .catch((e) => log("下载失败: " + e.message));
  links.appendChild(all);
}

async function init() {
  if (!localStorage.getItem("gnb_admin_token")) {
    const t = prompt("如服务端设置了 GNB_ADMIN_TOKEN，请输入；否则直接确定");
    if (t) localStorage.setItem("gnb_admin_token", t);
  }

  try {
    const data = await api("/api/network");
    fillForm(data);
    await refreshStatus();
  } catch (e) {
    log("加载配置失败: " + e.message);
  }

  $("btnAddClient").onclick = () => {
    const nextId = clients.length ? Math.max(...clients.map((c) => c.nodeid)) + 1 : 1002;
    clients.push({
      nodeid: nextId,
      listen: 9000 + nextId - 1000,
      tun_ip: `10.1.0.${nextId - 1000}`,
      lan_routes: [],
      ...CLIENT_ADV_DEFAULTS,
    });
    renderClients();
  };

  $("btnSave").onclick = async () => {
    try {
      await api("/api/network", { method: "POST", body: JSON.stringify(collectNetwork()) });
      log("配置已保存");
    } catch (e) {
      log("保存失败: " + e.message);
    }
  };

  $("btnGenerate").onclick = async () => {
    try {
      validateIndexPublicIp();
      const regenerateKeys = $("regenerateKeys").checked;
      if (
        regenerateKeys &&
        !confirm(
          "重新生成密钥后，所有已部署的内网节点需重新下载并替换配置包。确定继续？"
        )
      ) {
        return;
      }
      const r = await api("/api/generate", {
        method: "POST",
        body: JSON.stringify({
          ...collectNetwork(),
          regenerate_keys: regenerateKeys,
        }),
      });
      if (r.passcode) $("idxPasscode").value = r.passcode;
      const keyMsg =
        r.keys_regenerated?.length || r.keys_preserved?.length
          ? `；密钥：保留 ${(r.keys_preserved || []).join(",") || "无"}，新生成 ${(r.keys_regenerated || []).join(",") || "无"}`
          : "";
      log(`配置已生成，passcode=${r.passcode}，目录=${r.conf_dir}${keyMsg}`);
      if (regenerateKeys) $("regenerateKeys").checked = false;
      const clientIds = r.nodeids.filter((id) => id !== +$("idxNodeid").value);
      readyClientIds = new Set(clientIds);
      renderClients();
      showDownloads(clientIds);
    } catch (e) {
      log("生成失败: " + e.message);
    }
  };

  $("btnStart").onclick = async () => {
    try {
      validateIndexPublicIp();
      const r = await api("/api/start", {
        method: "POST",
        body: JSON.stringify(collectNetwork()),
      });
      log(r.message + (r.pid ? ` PID=${r.pid}` : ""));
      await refreshStatus();
      await refreshGnbLogs();
    } catch (e) {
      log("启动失败: " + e.message);
      try {
        const logs = await api("/api/logs");
        if (logs.log) log("gnb 日志:\n" + logs.log);
      } catch (_) {}
    }
  };

  $("btnStop").onclick = async () => {
    try {
      const r = await api("/api/stop", { method: "POST", body: "{}" });
      log(r.message);
      await refreshStatus();
    } catch (e) {
      log("停止失败: " + e.message);
    }
  };

  $("idxEnableForward").onchange = syncIndexForwardMode;
  $("idxNodeDetectWorker").onchange = syncIndexDetectIntervalField;
  $("idxGnbLogEnabled").onchange = syncGnbLogFields;
  $("idxPublicIp").oninput = () => {
    if ($("idxPublicIp").value.trim()) {
      $("idxPublicIp").classList.remove("input-invalid");
    }
  };

  $("btnRefresh").onclick = refreshStatus;
  $("btnGnbLogRefresh").onclick = refreshGnbLogs;
  $("gnbLogAutoRefresh").onchange = (e) => setGnbLogAutoRefresh(e.target.checked);
  $("btnDownloadAll").onclick = () =>
    downloadZip("/api/download/all", "gnb-clients.zip")
      .then(() => log("已下载全部内网节点配置包"))
      .catch((e) => log("下载失败: " + e.message));
}

init();
