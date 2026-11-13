/**
 * Build-time channel selected by the Vite `define`: a development build loads the
 * debug probe, a production/guard build folds the guard away and bundles no probe code.
 */
declare const __VELES_CHANNEL__: 'development' | 'production';

/** A Vite asset URL, used for the bundled Rust core (`veles_crypto_bg.wasm`). */
declare module '*.wasm?url' {
    const url: string;
    export default url;
}
