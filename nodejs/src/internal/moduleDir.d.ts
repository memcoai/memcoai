/**
 * Directory of this module, in whichever of the builds is running.
 *
 * There is no TypeScript implementation, and there cannot be one. `import.meta`
 * is a compile error under `module: commonjs` (TS1470) and `__dirname` type
 * checks in the ESM pass but throws `ReferenceError` at runtime — and `src/` is
 * compiled twice from a single copy, so no `.ts` file may name either. The one
 * differing line therefore lives in two plain `.js` files that tsc never reads
 * (`allowJs` is off), and this declaration is what `./moduleDir.js` resolves to
 * at compile time. The build copies the right one into place.
 */
export declare const moduleDir: string
