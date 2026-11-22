"""Public citations keep their destination while visible identities stay hidden."""

import unittest

from backend.contracts.modules.perception import ContextItem
from backend.infrastructure.model.public_text import PublicText

RESOURCE = "pdf-22a620c47cf498c7988a"
CARD = f"info-{RESOURCE}-252f663ef8b6"
URL = f"/workspace/sources/paper-{CARD}"
LABEL = "FCR 单证交易中货运代理人的交单义务研究"


def receipt(tool="information_card_read"):
    card = {"card_id": CARD, "resource_id": RESOURCE, "citation_url": URL}
    return ContextItem(
        type="tool_result",
        source="action",
        role="tool",
        body={
            "tool_name": tool,
            "outcome": "ok",
            "result": {
                "status": "ok",
                "source_type": "validated_information_card",
                **({"items": [card]} if tool.endswith("search") else {"card": card}),
            },
        },
        identity_labels={RESOURCE: LABEL},
    )


class PublicCitationTests(unittest.TestCase):
    def assert_all_splits(self, items, text, expected):
        for cut in range(len(text) + 1):
            public = PublicText(items)
            first = public.push(text[:cut])
            self.assertTrue(expected.startswith(first), (cut, first))
            actual = first + public.push(text[cut:]) + public.push("", final=True)
            self.assertEqual(actual, expected, cut)
        public = PublicText(items)
        self.assertEqual(
            "".join(public.push(char) for char in text) + public.push("", final=True),
            expected,
        )

    def test_verified_search_and_read_links_survive_render_and_every_stream_split(self):
        for tool in ("information_card_search", "information_card_read"):
            action = receipt(tool)
            candidate = action.model_copy(update={"type": "resource", "role": "user"})
            graph = ContextItem(
                type="resource", source="graph", kind="graph.tool",
                body={"output": {"action": action.body}},
                identity_labels={RESOURCE: LABEL},
            )
            for destination in (URL, f"<{URL}>"):
                for item in (action, candidate, graph):
                    with self.subTest(tool=tool, destination=destination, source=item.source):
                        text = f"可查阅[论文原文]({destination})。"
                        items = [item]
                        self.assertEqual(PublicText(items).render(text), text)
                        self.assertEqual(PublicText(items).render(PublicText(items).render(text)), text)
                        self.assert_all_splits(items, text, text)

    def test_visible_identity_and_link_title_are_still_projected(self):
        text = f"材料 {RESOURCE}；[{RESOURCE}]({URL})，编号 {RESOURCE}。"
        expected = f"材料 {LABEL}；[{LABEL}]({URL})，编号 {LABEL}。"
        self.assertEqual(PublicText([receipt()]).render(text), expected)
        self.assert_all_splits([receipt()], text, expected)

    def test_unverified_or_mismatched_targets_do_not_bypass_projection(self):
        user = receipt().model_copy(update={"type": "message", "source": "user"})
        other_tool = receipt()
        other_tool.body["tool_name"] = "read_material"
        failed = receipt()
        failed.body["outcome"] = "error"
        mismatch = receipt()
        mismatch.body["result"]["card"]["card_id"] = "another-card"
        model = ContextItem(
            type="resource", source="graph", kind="graph.model",
            body={"output": {"action": receipt().body}},
            identity_labels={RESOURCE: LABEL},
        )
        for item in (user, other_tool, failed, mismatch, model):
            with self.subTest(item=item):
                text = f"[论文原文]({URL})"
                expected = text.replace(RESOURCE, LABEL)
                self.assertEqual(PublicText([item]).render(text), expected)
                self.assert_all_splits([item], text, expected)
        for url in (URL + "-forged", "https://example.test" + URL):
            text = f"[其他链接]({url})"
            self.assertEqual(PublicText([receipt()]).render(text), text.replace(RESOURCE, LABEL))

    def test_stream_and_render_share_longest_match_without_changing_pending_state(self):
        item = receipt()
        item.identity_labels[RESOURCE + "-long"] = "完整材料"
        text = RESOURCE + "-long"
        public = PublicText([item])
        self.assertEqual(public.push(RESOURCE), "")
        self.assertEqual(public.render(f"[论文]({URL})"), f"[论文]({URL})")
        self.assertEqual(public.push("-long", final=True), "完整材料")
        self.assertEqual(public.render(text), "完整材料")
        self.assert_all_splits([item], text, "完整材料")


if __name__ == "__main__":
    unittest.main()
