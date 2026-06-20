from __future__ import annotations

from karst.review.diff import parse_diff


SAMPLE_DIFF = """\
diff --git a/src/orders.ts b/src/orders.ts
--- a/src/orders.ts
+++ b/src/orders.ts
@@ -10,4 +10,6 @@ export function checkout(cart: Cart) {
   const total = cart.lineItems.reduce((s, l) => s + l.price, 0);
   const lock = acquireLock(cart.id);
-  return chargeUser(cart.userId, total);
+  // mutate then lock — race
+  cart.total = total;
+  return chargeUser(cart.userId, cart.total);
 }
diff --git a/docs/README.md b/docs/README.md
new file mode 100644
--- /dev/null
+++ b/docs/README.md
@@ -0,0 +1,2 @@
+# Orders
+New module.
"""


def test_parse_diff_extracts_files_and_hunks() -> None:
    parsed = parse_diff(SAMPLE_DIFF)
    assert {f.path for f in parsed.files} == {"src/orders.ts", "docs/README.md"}

    orders = next(f for f in parsed.files if f.path == "src/orders.ts")
    assert not orders.is_added
    assert not orders.is_removed
    assert len(orders.hunks) == 1
    h = orders.hunks[0]
    assert h.new_start == 10
    assert h.has_additions
    # The three added lines in the post-image are 12, 13, 14 — adjacent.
    assert min(h.added_line_numbers) == 12
    assert max(h.added_line_numbers) == 14

    readme = next(f for f in parsed.files if f.path == "docs/README.md")
    assert readme.is_added
    assert readme.is_reviewable
    assert readme.hunks[0].added_line_numbers == (1, 2)


def test_added_line_ranges_collapse_to_runs() -> None:
    parsed = parse_diff(SAMPLE_DIFF)
    orders = next(f for f in parsed.files if f.path == "src/orders.ts")
    assert orders.added_line_ranges() == [(12, 14)]


def test_empty_diff_returns_empty() -> None:
    parsed = parse_diff("")
    assert parsed.files == []
    assert parsed.reviewable_files() == []
