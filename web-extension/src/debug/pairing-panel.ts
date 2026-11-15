// OTP-10 D5: the Chrome pairing debug panel.
//
// This module is loaded through a dynamic import behind the build-time
// `__VELES_CHANNEL__` constant on the connector page. A production build folds the
// guard and never emits the chunk. The panel only drives the entry points the connector
// already owns; it never touches a session engine or the wire directly.
//
// OTP-11 D9 adds the peer-management controls: the peer list with Revoke and Mutual remove,
// the confirmed endpoint-wide Reset and the one trust status the connector reports. Every
// action is the connector's revocation coordinator, never a direct trust-store write.
//
// The six-digit code is read from the input only to hand it to `start`. It is never
// rendered, logged, pushed into a diagnostic ring or placed in a view model, and the
// input is cleared as soon as pairing starts.

import type { PeerSessionKind, TrustStatus } from '../connector/revocation';
import type { RemovalResult } from '../connector/sessions';

export const PAIRING_PANEL_TEST_ID = 'veles-pairing-debug';

const STATUS_POLL_MS = 500;

/** The one destructive-action warning; also the confirmation prompt's text. */
const RESET_WARNING =
    'Reset is destructive: it deletes every trusted peer and this extension identity.';

/** The local-only outcome when no full session of the peer is live. */
const KEPT_NOT_CONNECTED =
    'No open full session: only local removal is possible. Nothing was deleted.';

/** Appended only when the blocking generation is a live recovery session. */
const RECOVERY_REMOVAL_HINT = 'disconnect and reconnect for a full session';

/** One row's live-session text: a recovery generation is not a removal destination. */
const SESSION_TEXT: Record<PeerSessionKind, string> = {
    full: 'full session open',
    recovery: 'recovery session open: disconnect and reconnect for mutual removal',
    none: 'no live session',
};

export interface PairingPanelDevice {
    handle: string;
    label: string;
}

export interface PairingPanelStatus {
    phase: string;
    finished: boolean;
    fingerprint?: string;
}

/** One listing entry (design D5): no key material, hint or native handle. */
export interface PairingPanelPeer {
    installationId: string;
    label: string;
    state: string;
    session: PeerSessionKind;
    fingerprint?: string;
}

/** The revocation-coordinator surface every control uses. */
export interface PairingPanelTrust {
    peers(): Promise<readonly PairingPanelPeer[]>;
    revoke(peerId: string): Promise<void>;
    removeMutually(peerId: string): Promise<RemovalResult>;
    reset(): Promise<void>;
    /** The status the connector reports; the panel never invents one. */
    status(): TrustStatus;
}

export interface PairingPanelDeps {
    devices(): readonly PairingPanelDevice[];
    status(): PairingPanelStatus;
    trust: PairingPanelTrust;
    start(deviceHandle: string, code: string): Promise<boolean>;
    /** Resolves false when the cancel cleanup (pending-record revoke) failed. */
    cancel(): Promise<boolean>;
    onChange(listener: () => void): () => void;
    /** The destructive-action confirmation; defaults to "not confirmed" so it never deletes. */
    confirm?(message: string): boolean;
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
    reset: HTMLButtonElement;
}

function button(document: Document, label: string): HTMLButtonElement {
    const element = document.createElement('button');
    element.type = 'button';
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

    const start = button(document, 'Start pairing');
    start.setAttribute('id', 'pairing-debug-start');
    const cancel = button(document, 'Cancel pairing');
    cancel.setAttribute('id', 'pairing-debug-cancel');

    const peers = document.createElement('ul');
    peers.setAttribute('aria-label', 'Known pairing records');
    const peerHeading = document.createElement('h3');
    peerHeading.textContent = 'Known peers';

    const warning = document.createElement('p');
    warning.textContent = RESET_WARNING;
    const reset = button(document, 'Reset identity');
    reset.setAttribute('data-action', 'reset-identity');

    const controls = document.createElement('div');
    controls.append(codeInput, deviceSelect, start, cancel);
    section.append(heading, status, controls, peerHeading, peers, warning, reset);
    return { section, status, codeInput, deviceSelect, start, cancel, peers, reset };
}

function renderStatus(
    status: HTMLElement,
    value: PairingPanelStatus,
    trust: TrustStatus,
    notice: string,
): void {
    let text = `State: ${value.phase} · Trust: ${trust}`;
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
    actions: {
        revoke(peerId: string): void;
        removeMutually(peerId: string): void;
        clear(peerId: string): void;
    },
): void {
    const document = list.ownerDocument;
    const rows = peers.map((peer) => {
        const item = document.createElement('li');
        const label = document.createElement('span');
        const session = SESSION_TEXT[peer.session];
        const fingerprint =
            peer.fingerprint === undefined ? '' : ` · Peer fingerprint: ${peer.fingerprint}`;
        label.textContent = `${peer.label} · ${peer.state}${fingerprint} · ${session}`;
        const revokeButton = button(document, 'Revoke peer');
        revokeButton.setAttribute('data-action', 'revoke-peer');
        revokeButton.addEventListener('click', () => actions.revoke(peer.installationId));
        const mutualButton = button(document, 'Mutual remove');
        mutualButton.setAttribute('data-action', 'mutual-remove');
        mutualButton.addEventListener('click', () => actions.removeMutually(peer.installationId));
        const clearButton = button(document, 'Clear incomplete record');
        // One per peer row: no shared DOM id, and the peer id stays out of the label.
        clearButton.setAttribute('data-action', 'clear-incomplete-record');
        clearButton.addEventListener('click', () => actions.clear(peer.installationId));
        item.append(label, revokeButton, mutualButton, clearButton);
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
    // A panel that is handed no confirmation seam deletes nothing: fail safe, never guess.
    const confirm = deps.confirm ?? (() => false);

    let notice = '';
    const refreshStatus = (): void =>
        renderStatus(elements.status, deps.status(), deps.trust.status(), notice);
    const refreshDevices = (): void => renderDevices(elements.deviceSelect, deps.devices());
    const refreshPeers = async (): Promise<void> => {
        try {
            renderPeers(elements.peers, await deps.trust.peers(), {
                revoke: revokePeer,
                removeMutually: removePeer,
                clear: clearPeer,
            });
        } catch {
            elements.peers.replaceChildren();
            notice = 'Peers unavailable: trust store unreadable';
            refreshStatus();
        }
    };

    /** Re-reads the list and the status after every action; a failure stays in the notice. */
    const settle = (message: string): void => {
        notice = message;
        refreshStatus();
        void refreshPeers();
    };

    const revokePeer = (peerId: string): void => {
        void deps.trust.revoke(peerId).then(
            () => settle('Peer revoked'),
            () => settle('Revoke failed: trust store unavailable'),
        );
    };

    /**
     * True only when the peer's live generation is recovery-only, so the local-only outcome can
     * name what unblocks a full session. A read failure never changes the notice.
     */
    const recoverySessionLive = async (peerId: string): Promise<boolean> => {
        try {
            const peers = await deps.trust.peers();
            return peers.some(
                (peer) => peer.installationId === peerId && peer.session === 'recovery',
            );
        } catch {
            return false;
        }
    };

    const removePeer = (peerId: string): void => {
        void deps.trust.removeMutually(peerId).then(
            async (result) => {
                if (result === 'removed') {
                    settle('Peer removed on both devices');
                    return;
                }
                if (result === 'kept-not-connected') {
                    // The usual reason is a live recovery generation, whose fixed class cannot
                    // carry a removal record: only a reconnect reaches a full session.
                    const recovery = await recoverySessionLive(peerId);
                    settle(
                        recovery
                            ? `${KEPT_NOT_CONNECTED} — ${RECOVERY_REMOVAL_HINT}.`
                            : KEPT_NOT_CONNECTED,
                    );
                    return;
                }
                settle('No response: the other device may keep its record');
            },
            () => settle('Mutual remove failed'),
        );
    };

    const clearPeer = (peerId: string): void => {
        // The clear is the coordinator's local revoke, so an open session of that peer ends
        // instead of the clear being refused.
        void deps.trust.revoke(peerId).then(
            () => settle('Cleared incomplete record'),
            () => settle('Clear failed: trust store unavailable'),
        );
    };

    const resetIdentity = (): void => {
        if (!confirm(RESET_WARNING)) {
            settle('Reset not confirmed: nothing was deleted');
            return;
        }
        void deps.trust.reset().then(
            () => settle('Identity reset; pair again to create new trust'),
            () => settle('Reset failed: trust store unavailable'),
        );
    };

    const begin = (): void => {
        const code = elements.codeInput.value.trim();
        const deviceHandle = elements.deviceSelect.value;
        // Never keep the code in the DOM after the attempt starts.
        elements.codeInput.value = '';
        if (!/^\d{6}$/.test(code) || deviceHandle === '') {
            settle('Enter a six-digit code and choose a phone');
            return;
        }
        void deps.start(deviceHandle, code).then(
            (started) => settle(started ? 'Pairing started' : 'Pairing failed'),
            () => settle('Pairing failed'),
        );
    };

    const stop = (): void => {
        void deps.cancel().then(
            (cleaned) =>
                settle(cleaned ? 'Pairing cancelled' : 'Pairing cancelled, cancel cleanup failed'),
            () => settle('Pairing cancelled, cancel cleanup failed'),
        );
    };

    elements.start.addEventListener('click', begin);
    elements.cancel.addEventListener('click', stop);
    elements.reset.addEventListener('click', resetIdentity);

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
