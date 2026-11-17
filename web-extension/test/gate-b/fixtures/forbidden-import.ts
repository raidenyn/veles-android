// Negative fixture for the Gate B scripted-client import guard.
//
// The guard test runs its closure check directly over this file; reaching
// `src/connector/**` from a client file must be reported as a violation. The
// fixture is intentionally outside the scanned `test/gate-b/*.ts` set.
import { NativeConnectorPort } from '../../../src/connector/port';

export const forbiddenFixturePort = NativeConnectorPort;
