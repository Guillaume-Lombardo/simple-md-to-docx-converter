import assert from "node:assert/strict";
import test from "node:test";

import { cookieValue } from "./browser-next-admin-helpers.mjs";

test("administration CSRF cookie parsing preserves the complete value", () => {
  assert.equal(
    cookieValue(
      "first=ignored; __Host-md_converter_csrf=token%2Bvalue==; last=ignored",
      "__Host-md_converter_csrf",
    ),
    "token%2Bvalue==",
  );
});

test("administration CSRF cookie parsing requires the exact cookie name", () => {
  assert.equal(
    cookieValue(
      "prefix__Host-md_converter_csrf=wrong; __Host-md_converter_csrf=right",
      "__Host-md_converter_csrf",
    ),
    "right",
  );
  assert.equal(cookieValue("unrelated=value", "missing"), undefined);
});
