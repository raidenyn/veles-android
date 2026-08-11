import {
  COMMAND_UUID,
  EVENT_UUID,
  FrameReassembler,
  SERVICE_UUID,
  constantTimeEqual,
  decodeMessage,
  encodeMessage,
  hmacProof,
  randomBase64Url,
  runProtocolSelfCheck,
  splitMessage,
} from "./protocol.mjs";

const MAX_VISIBLE_OTPS = 20;
const MAX_LOG_ENTRIES = 100;
const AUTH_TIMEOUT_MS = 10000;
const HEARTBEAT_INTERVAL_MS = 5000;

class PhoneConnection {
  constructor(device, ui, log, onClosed) {
    this.device = device;
    this.ui = ui;
    this.log = log;
    this.onClosed = onClosed;
    this.server = null;
    this.command = null;
    this.events = null;
    this.authenticated = false;
    this.phoneLabel = device.name || "Unnamed phone";
    this.messageId = 1;
    this.writeChain = Promise.resolve();
    this.reassembler = new FrameReassembler();
    this.authentication = null;
    this.clientNonce = null;
    this.heartbeatTimer = null;
    this.closed = false;
    this.onNotification = this.handleNotification.bind(this);
    this.onDisconnected = () => this.disconnect("GATT disconnected");
  }

  async connect() {
    this.device.addEventListener("gattserverdisconnected", this.onDisconnected);
    this.server = await this.device.gatt.connect();
    const service = await this.server.getPrimaryService(SERVICE_UUID);
    this.command = await service.getCharacteristic(COMMAND_UUID);
    this.events = await service.getCharacteristic(EVENT_UUID);
    this.events.addEventListener("characteristicvaluechanged", this.onNotification);
    await this.events.startNotifications();

    this.clientNonce = randomBase64Url(16);
    this.authentication = Promise.withResolvers();
    const timeout = setTimeout(
      () => this.authentication.reject(new Error("Authentication timed out")),
      AUTH_TIMEOUT_MS,
    );
    await this.send({ type: "hello", clientNonce: this.clientNonce });
    try {
      await this.authentication.promise;
    } finally {
      clearTimeout(timeout);
    }
  }

  async send(message) {
    const messageId = this.messageId;
    this.messageId = this.messageId === 0xffff ? 1 : this.messageId + 1;
    const frames = splitMessage(messageId, encodeMessage(message));
    this.writeChain = this.writeChain.then(async () => {
      for (const frame of frames) await this.command.writeValueWithResponse(frame);
    });
    return this.writeChain;
  }

  async pull() {
    if (!this.authenticated) throw new Error("Phone is not authenticated");
    await this.send({ type: "pull" });
  }

  disconnect(reason, kind = "error") {
    if (this.closed) return;
    this.closed = true;
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    this.authenticated = false;
    this.events?.removeEventListener("characteristicvaluechanged", this.onNotification);
    this.device.removeEventListener("gattserverdisconnected", this.onDisconnected);
    if (this.device.gatt.connected) this.device.gatt.disconnect();
    this.ui.setStatus(reason, kind);
    this.onClosed(this.device.id);
  }

  async handleNotification(event) {
    let complete;
    try {
      const bytes = new Uint8Array(
        event.target.value.buffer,
        event.target.value.byteOffset,
        event.target.value.byteLength,
      ).slice();
      complete = this.reassembler.accept(this.device.id, bytes);
      if (!complete) return;
      const message = decodeMessage(complete);
      await this.handleMessage(message);
    } catch (error) {
      this.authentication?.reject(error);
      this.log.append(`${this.phoneLabel}: ${error.name || "Error"}: ${error.message}`);
      this.disconnect(`${error.name || "Error"}: ${error.message}`);
    }
  }

  async handleMessage(message) {
    if (message.type === "challenge") {
      for (const field of ["clientNonce", "serverNonce", "sessionId", "proof"]) {
        if (typeof message[field] !== "string") throw new Error(`Challenge missing ${field}`);
      }
      if (message.clientNonce !== this.clientNonce) throw new Error("Challenge client nonce changed");
      const expected = await hmacProof(
        "server",
        message.clientNonce,
        message.serverNonce,
        message.sessionId,
      );
      if (!constantTimeEqual(expected, message.proof)) throw new Error("Server proof rejected");
      const proof = await hmacProof(
        "client",
        message.clientNonce,
        message.serverNonce,
        message.sessionId,
      );
      await this.send({ type: "authenticate", proof });
      return;
    }

    if (message.type === "authenticated") {
      if (typeof message.phoneLabel !== "string") throw new Error("Authenticated message missing phone label");
      this.authenticated = true;
      this.phoneLabel = message.phoneLabel;
      this.ui.setLabel(message.phoneLabel);
      this.ui.setStatus("Authenticated", "success");
      this.authentication.resolve();
      this.heartbeatTimer = setInterval(
        () => {
          this.reassembler.expire();
          this.send({ type: "heartbeat" }).catch((error) => this.disconnect(error.message));
        },
        HEARTBEAT_INTERVAL_MS,
      );
      await this.pull();
      return;
    }

    if (message.type === "otp") {
      for (const field of ["delivery", "eventId", "code", "merchant", "amount", "currency", "phoneLabel"]) {
        if (message[field] === null || message[field] === undefined) throw new Error(`OTP missing ${field}`);
      }
      await this.ui.addOtp(message);
      return;
    }

    if (message.type === "error") {
      throw new Error(`Android error: ${message.errorCode || "unknown"}`);
    }
    throw new Error(`Unsupported message type: ${message.type}`);
  }
}

class PhoneCardUi {
  constructor(card, log, otpContainer, copyNextPushCheckbox) {
    this.card = card;
    this.log = log;
    this.otpContainer = otpContainer;
    this.copyNextPushCheckbox = copyNextPushCheckbox;
    this.pullButton = card.querySelector('[data-action="pull"]');
    this.disconnectButton = card.querySelector('[data-action="disconnect"]');
    this.statusEl = card.querySelector('[data-field="phone-status"]');
    this.labelEl = card.querySelector('[data-field="phone-label"]');

    this.disconnectButton.addEventListener("click", () => {
      if (typeof this.onDisconnect === "function") this.onDisconnect();
    });
    this.pullButton.addEventListener("click", () => {
      if (typeof this.onPull === "function") this.onPull();
    });
  }

  setStatus(text, kind) {
    this.statusEl.textContent = text;
    this.statusEl.classList.remove("status-error", "status-success", "status-checking");
    if (kind === "success") {
      this.statusEl.classList.add("status-success");
      this.pullButton.disabled = false;
      this.card.dataset.state = "success";
    } else if (kind === "error") {
      this.statusEl.classList.add("status-error");
      this.card.dataset.state = "error";
      this.pullButton.disabled = true;
    } else {
      this.card.dataset.state = "";
      this.pullButton.disabled = true;
    }
  }

  setLabel(text) {
    this.labelEl.textContent = text;
  }

  addOtp(message) {
    const template = document.getElementById("otp-template");
    const node = template.content.cloneNode(true);
    node.querySelector('[data-field="delivery"]').textContent = String(message.delivery);
    node.querySelector('[data-field="source"]').textContent = String(message.phoneLabel);
    node.querySelector('[data-field="code"]').textContent = String(message.code);
    node.querySelector('[data-field="merchant"]').textContent = String(message.merchant);
    node.querySelector('[data-field="amount"]').textContent = String(message.amount);
    node.querySelector('[data-field="currency"]').textContent = String(message.currency);
    node.querySelector('[data-field="event-id"]').textContent = String(message.eventId);
    const copyButton = node.querySelector('[data-action="copy"]');
    copyButton.addEventListener("click", () => {
      copyToClipboardManual(String(message.code), this.log);
    });
    this.otpContainer.prepend(node);
    while (this.otpContainer.childElementCount > MAX_VISIBLE_OTPS) {
      this.otpContainer.removeChild(this.otpContainer.lastElementChild);
    }
    if (message.delivery === "push") {
      copyNextPushOneShot(String(message.code), this.log, this.copyNextPushCheckbox);
    }
  }

  remove() {
    this.card.remove();
  }
}

async function copyToClipboardManual(code, log) {
  try {
    await navigator.clipboard.writeText(code);
    log.append(`Copied ${code}`);
  } catch (error) {
    log.append(`Copy failed: ${error.name}: ${error.message}`);
  }
}

function copyNextPushOneShot(code, log, copyNextPushCheckbox) {
  if (!copyNextPushCheckbox.checked) return;
  copyNextPushCheckbox.checked = false;
  (async () => {
    try {
      await navigator.clipboard.writeText(code);
      log.append(`Auto-copied ${code}`);
    } catch (error) {
      log.append(`Auto-copy failed: ${error.name}: ${error.message}`);
    }
  })();
}

class EventLog {
  constructor(listEl) {
    this.listEl = listEl;
  }

  append(text) {
    const item = document.createElement("li");
    item.textContent = `${new Date().toISOString()}  ${text}`;
    this.listEl.prepend(item);
    while (this.listEl.childElementCount > MAX_LOG_ENTRIES) {
      this.listEl.removeChild(this.listEl.lastElementChild);
    }
  }
}

const phonesContainer = document.getElementById("phones");
const otpContainer = document.getElementById("otp-events");
const eventLogEl = document.getElementById("event-log");
const connectButton = document.getElementById("connect-phone");
const copyNextPushCheckbox = document.getElementById("copy-next-push");
const selfCheckEl = document.getElementById("self-check");

const log = new EventLog(eventLogEl);
const connections = new Map();

function setSelfCheck(text, kind) {
  selfCheckEl.textContent = text;
  selfCheckEl.classList.remove("status-checking", "status-error", "status-success", "status-warning");
  if (kind) selfCheckEl.classList.add(`status-${kind}`);
}

function disableConnect(text) {
  connectButton.disabled = true;
  if (text) {
    setSelfCheck(text, "error");
    log.append(text);
  }
}

async function startupSelfCheck() {
  if (!navigator.bluetooth) {
    disableConnect("Web Bluetooth unavailable in this browser");
    return;
  }
  if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
    disableConnect("Clipboard API unavailable");
    return;
  }
  try {
    await runProtocolSelfCheck();
    setSelfCheck("Protocol self-check passed", "success");
    connectButton.disabled = false;
  } catch (error) {
    disableConnect(`Protocol self-check failed: ${error.message}`);
  }
}

function removePhone(deviceId) {
  const entry = connections.get(deviceId);
  if (entry) {
    entry.ui.remove();
    connections.delete(deviceId);
    log.append(`Removed phone ${entry.connection.phoneLabel}`);
  }
}

async function connectPhone() {
  let device;
  try {
    device = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SERVICE_UUID] }],
    });
  } catch (error) {
    if (error.name === "NotFoundError") {
      log.append("Device selection cancelled");
    } else {
      log.append(`Device selection failed: ${error.name}: ${error.message}`);
      setSelfCheck(`Device selection failed: ${error.message}`, "error");
    }
    return;
  }

  if (connections.has(device.id)) {
    log.append(`Phone ${device.name || device.id} already connected`);
    return;
  }

  const template = document.getElementById("phone-template");
  const node = template.content.cloneNode(true);
  const card = node.querySelector(".phone-card");
  phonesContainer.append(node);
  const ui = new PhoneCardUi(card, log, otpContainer, copyNextPushCheckbox);
  const connection = new PhoneConnection(device, ui, log, removePhone);
  connections.set(device.id, { connection, ui });

  ui.onPull = async () => {
    try {
      await connection.pull();
      log.append(`Pulled from ${connection.phoneLabel}`);
    } catch (error) {
      log.append(`Pull failed: ${error.name}: ${error.message}`);
      connection.disconnect(`${error.name}: ${error.message}`);
    }
  };

  ui.onDisconnect = () => {
    connection.disconnect("User disconnected", "info");
  };

  try {
    await connection.connect();
    log.append(`Connected ${connection.phoneLabel}`);
  } catch (error) {
    log.append(`Connect failed: ${error.name}: ${error.message}`);
    connection.disconnect(`${error.name}: ${error.message}`);
  }
}

connectButton.addEventListener("click", connectPhone);

window.addEventListener("pagehide", () => {
  for (const { connection } of connections.values()) {
    connection.disconnect("Popup closed", "info");
  }
});

startupSelfCheck();