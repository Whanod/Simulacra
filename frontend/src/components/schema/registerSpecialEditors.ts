/**
 * Module-load side effect: wires built-in special-editor plugins
 * into the registry so any consumer that imports this file gets a
 * populated registry without having to know which plugins exist.
 *
 * Import this file once at the app boundary (e.g. from the schema
 * preview page and, later, from the real builder entrypoint). Every
 * new plugin goes through `registerSpecialEditor(key, component)`
 * here so there is exactly one place to audit the mapping.
 *
 * US-010 only ships the `noop-preview` fixture. US-011 adds the
 * real `world-markets-graph` plugin in this same file.
 */

import { registerSpecialEditor } from "./specialEditors";
import { NoopSpecialEditor } from "./NoopSpecialEditor";
import { WorldMarketsGraphEditor } from "./WorldMarketsGraphEditor";

registerSpecialEditor("noop-preview", NoopSpecialEditor);
registerSpecialEditor("world-markets-graph", WorldMarketsGraphEditor);
