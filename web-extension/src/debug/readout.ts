/** The development-only readout's stable hook for browser and unit tests. */
export const DEBUG_READOUT_TEST_ID = 'veles-debug-readout';

export interface DebugReadoutEvent {
    marker: string;
    eventId: string;
    sessionIdPrefix: string;
}

export interface DebugReadout {
    show(event: DebugReadoutEvent): void;
    clear(): void;
}

/**
 * The connector page's debug readout (design D5). It shows only synthetic probe data and is
 * created by the development probe alone, so a production bundle never contains it.
 */
export function createDebugReadout(document: Document): DebugReadout {
    const section = document.createElement('section');
    section.dataset.testid = DEBUG_READOUT_TEST_ID;
    section.hidden = true;
    document.body.append(section);
    return {
        show(event) {
            section.hidden = false;
            section.textContent =
                `marker ${event.marker} · event ${event.eventId} · ` +
                `session ${event.sessionIdPrefix}`;
        },
        clear() {
            section.hidden = true;
            section.textContent = '';
        },
    };
}
