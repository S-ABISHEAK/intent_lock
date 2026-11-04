import { describe, expect, it } from "vitest";

import { parseChain } from "../src/logic/chain";

const VALID = "shipping_address_id=ADDR-0002 sources=['r-12', 'r-13'] via get_reviews#1 reviews[0].text -> get_saved_addresses#2 addresses[1].id";

describe("parseChain accepts only the backend's formats", () => {
  it("parses a valid chain", () => {
    expect(parseChain(VALID)).toEqual({
      kind: "sources",
      field: "shipping_address_id",
      value: "ADDR-0002",
      sources: ["r-12", "r-13"],
      chains: [
        [
          { tool: "get_reviews", seq: 1, path: "reviews[0].text" },
          { tool: "get_saved_addresses", seq: 2, path: "addresses[1].id" },
        ],
      ],
    });
  });

  it("parses a detail produced by the real backend (taint.py, D-048 search influence)", () => {
    const c = parseChain("sku=LAP-1209 sources=['s:2:0:d'] via get_reviews#1 reviews[0].text -> search_products#2 results[0].sku");
    expect(c?.sources).toEqual(["s:2:0:d"]);
    expect(c?.chains[0].map((s) => s.tool)).toEqual(["get_reviews", "search_products"]);
  });

  it("parses several chains joined by '; '", () => {
    const c = parseChain("to=a@b.c sources=['r1'] via get_reviews#1 reviews[0].text; get_product#3 description");
    expect(c?.chains).toHaveLength(2);
  });

  it("parses the 'also named by untrusted content' format", () => {
    const c = parseChain("to=a@b.c is also named by untrusted content ['r9'] via get_reviews#4 reviews[2].text");
    expect(c?.kind).toBe("also_named");
    expect(c?.sources).toEqual(["r9"]);
  });

  it.each([
    ["no via", "shipping_address_id=ADDR-0002 sources=['r-12']"],
    ["an empty via", "x=1 sources=['r'] via "],
    ["a trailing arrow", "x=1 sources=['r'] via get_reviews#1 text ->"],
    ["a step without #n", "x=1 sources=['r'] via get_reviews text"],
    ["a non-integer seq", "x=1 sources=['r'] via get_reviews#one text"],
    ["an empty step in a chain", "x=1 sources=['r'] via get_reviews#1 a ->  -> get_product#2 b"],
    ["extra text after a step", "x=1 sources=['r'] via get_reviews#1 text and more"],
    ["unquoted sources", "x=1 sources=[r1] via get_reviews#1 text"],
    ["an UNKNOWN_PROVENANCE detail", "shipping_address_id=ADDR-0009"],
    ["free text", "the agent was told to do it"],
  ])("returns null for %s, with nothing repaired", (_n, d) => {
    expect(parseChain(d)).toBeNull();
  });

  it("returns null for non-strings", () => {
    expect(parseChain(undefined)).toBeNull();
    expect(parseChain(null)).toBeNull();
    expect(parseChain(42)).toBeNull();
  });
});
