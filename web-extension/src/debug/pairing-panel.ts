// OTP-10 D5: the Chrome pairing debug panel.
//
// This module is loaded through a dynamic import behind the build-time
// `__VELES_CHANNEL__` constant on the connector page. A production build folds the
// guard and never emits the chunk. The panel only drives the enrollment entry points
// the connector already owns; it never touches a session engine or the wire directly.
//
// The six-digit code is read from the input only to hand it to `start`. It is never
// rendered, logged, pushed into a diagnostic ring or placed in a view model, and the
// input is cleared as soon as pairing starts.

export const PAIRING_PANEL_TEST_ID = 'veles-pairing-debug';

const STATUS_POLL_MS = 500;

export interface PairingPanelDevice {
    handle: string;
    label: string;
}

export interface PairingPanelStatus {
    phase: string;
    finished: boolean;
    fingerprint?: string;
}

export interface PairingPanelPeer {
    installationId: string;
    label: string;
    state: string;
}

/** The trust store surface the Clear control uses (`repository.revoke`). */
export interface PairingPanelTrust {
    peers(): Promise<readonly PairingPanelPeer[]>;
    revoke(peerId: string): Promise<void>;
}

export interface PairingPanelDeps {
    devices(): readonly PairingPanelDevice[];
    status(): PairingPanelStatus;
    trust: PairingPanelTrust;
    start(deviceHandle: string, code: string): Promise<boolean>;
    /** Resolves false when the cancel cleanup (pending-record revoke) failed. */
    cancel(): Promise<boolean>;
    onChange(listener: () => void): () => void;
    /** Injection seams for the unit tests; production uses the document and timers. */
    document?: Document;
    setInterval?: (fn: () => void, ms: number) => unknown;
}

interface PanelElements {
    section: HTMLElement;
    status: HTMLElement;
    codeInput: HTMLInputElement;
    deviceSelect: HTMLSelectElement;
    start: HTMLButtonElement;
    cancel: HTMLButtonElement;
    peers: HTMLUListElement;
}

function button(document: Document, id: string, label: string): HTMLButtonElement {
    const element = document.createElement('button');
    element.type = 'button';
    element.id = id;
    element.textContent = label;
    return element;
}

function createElements(document: Document): PanelElements {
    const section = document.createElement('section');
    section.dataset.testid = PAIRING_PANEL_TEST_ID;
    section.setAttribute('aria-label', 'Veles pairing debug');

    const heading = document.createElement('h2');
    heading.textContent = 'Veles pairing debug';

    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');

    const codeInput = document.createElement('input');
    codeInput.type = 'text';
    codeInput.maxLength = 6;
    codeInput.setAttribute('inputmode', 'numeric');
    codeInput.setAttribute('autocomplete', 'off');
    codeInput.setAttribute('aria-label', 'Pairing code');

    const deviceSelect = document.createElement('select');
    deviceSelect.setAttribute('aria-label', 'Phone to pair');

    const start = button(document, 'pairing-debug-start', 'Start pairing');
    const cancel = button(document, 'pairing-debug-cancel', 'Cancel pairing');

    const peers = document.createElement('ul');
    peers.setAttribute('aria-label', 'Known pairing records');

    const controls = document.createElement('div');
    controls.append(codeInput, deviceSelect, start, cancel);
    const peerHeading = document.createElement('h3');
    peerHeading.textContent = 'Clear incomplete record';
    section.append(heading, status, controls, peerHeading, peers);
    return { section, status, codeInput, deviceSelect, start, cancel, peers };
}

function renderStatus(status: HTMLElement, value: PairingPanelStatus, notice: string): void {
    let text = `State: ${value.phase}`;
    if (value.fingerprint !== undefined) text += ` · Fingerprint: ${value.fingerprint}`;
    if (value.finished) text += ' · finished';
    if (notice !== '') text += ` · ${notice}`;
    status.textContent = text;
}

function renderDevices(select: HTMLSelectElement, devices: readonly PairingPanelDevice[]): void {
    const document = select.ownerDocument;
    const options = devices.map((device) => {
        const option = document.createElement('option');
        option.value = device.handle;
        option.textContent = device.label;
        return option;
    });
    select.replaceChildren(...options);
}

function renderPeers(
    list: HTMLUListElement,
    peers: readonly PairingPanelPeer[],
    clear: (peerId: string) => void,
): void {
    const document = list.ownerDocument;
    const rows = peers.map((peer) => {
        const item = document.createElement('li');
        const label = document.createElement('span');
        label.textContent = `${peer.label} · ${peer.state}`;
        const clearButton = button(document, 'pairing-debug-clear', 'Clear incomplete record');
        clearButton.addEventListener('click', () => clear(peer.installationId));
        item.append(label, clearButton);
        return item;
    });
    list.replaceChildren(...rows);
}

/**
 * Attaches the panel to [host]. It is a no-op outside the development channel because
 * `connector.ts` only imports this module from inside the compile-time guard.
 */
export function mountPairingPanel(host: HTMLElement, deps: PairingPanelDeps): void {
    const document = deps.document ?? host.ownerDocument;
    const elements = createElements(document);
    host.append(elements.section);

    let notice = '';
    const refreshStatus = (): void => renderStatus(elements.status, deps.status(), notice);
    const refreshDevices = (): void => renderDevices(elements.deviceSelect, deps.devices());
    const refreshPeers = async (): Promise<void> => {
        try {
            renderPeers(elements.peers, await deps.trust.peers(), clearPeer);
        } catch {
            elements.peers.replaceChildren();
            notice = 'Clear unavailable: trust store unreadable';
            refreshStatus();
        }
    };

    const clearPeer = (peerId: string): void => {
        void deps.trust.revoke(peerId).then(
            () => {
                notice = 'Cleared incomplete record';
                refreshStatus();
                void refreshPeers();
            },
            () => {
                notice = 'Clear failed: trust store unavailable';
                refreshStatus();
            },
        );
    };

    const begin = (): void => {
        const code = elements.codeInput.value.trim();
        const deviceHandle = elements.deviceSelect.value;
        // Never keep the code in the DOM after the attempt starts.
        elements.codeInput.value = '';
        if (!/^\d{6}$/.test(code) || deviceHandle === '') {
            notice = 'Enter a six-digit code and choose a phone';
            refreshStatus();
            return;
        }
        void deps.start(deviceHandle, code).then(
            (started) => {
                notice = started ? 'Pairing started' : 'Pairing failed';
                refreshStatus();
            },
            () => {
                notice = 'Pairing failed';
                refreshStatus();
            },
        );
    };

    const stop = (): void => {
        void deps.cancel().then(
            (cleaned) => {
                notice = cleaned ? 'Pairing cancelled' : 'Pairing cancelled, cancel cleanup failed';
                refreshStatus();
            },
            () => {
                notice = 'Pairing cancelled, cancel cleanup failed';
                refreshStatus();
            },
        );
    };

    elements.start.addEventListener('click', begin);
    elements.cancel.addEventListener('click', stop);

    deps.onChange(() => {
        refreshDevices();
        refreshStatus();
        void refreshPeers();
    });
    const schedule =
        deps.setInterval ?? ((fn: () => void, ms: number) => globalThis.setInterval(fn, ms));
    schedule(() => {
        refreshStatus();
        refreshDevices();
    }, STATUS_POLL_MS);
    refreshDevices();
    refreshStatus();
    void refreshPeers();
}
