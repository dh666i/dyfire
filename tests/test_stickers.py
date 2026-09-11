import unittest

from core.stickers import (
    NativeStickerPreparationError,
    NativeStickerSendResult,
    send_douyin_sticker,
)


class _Locator:
    def __init__(self, *, visible=True, click_error=None, value="续火花"):
        self.first = self
        self.visible = visible
        self.click_error = click_error
        self.value = value
        self.pressed = []
        self.clicks = 0

    async def count(self):
        return 1 if self.visible else 0

    def nth(self, _index):
        return self

    async def is_visible(self):
        return self.visible

    async def click(self, **_kwargs):
        self.clicks += 1
        if self.click_error:
            raise self.click_error

    async def press(self, key):
        self.pressed.append(key)

    def locator(self, _selector):
        return self

    def get_by_role(self, *_args, **_kwargs):
        return self

    def get_by_text(self, *_args, **_kwargs):
        return self

    async def inner_text(self):
        return self.value

    async def get_attribute(self, name):
        if name == "src":
            return "https://example.invalid/sticker-resource"
        return self.value if name in {"aria-label", "title", "alt"} else None


class _Keyboard:
    def __init__(self):
        self.pressed = []

    async def press(self, key):
        self.pressed.append(key)


class _Panel(_Locator):
    def __init__(self, item, *, visible=True):
        super().__init__(visible=visible)
        self.item = item

    def locator(self, _selector):
        return self.item

    def get_by_role(self, *_args, **_kwargs):
        return self.item

    def get_by_text(self, *_args, **_kwargs):
        return self.item


class _Page:
    def __init__(self, *, item_error=None, panel_visible=True):
        self.button = _Locator()
        self.item = _Locator(click_error=item_error)
        self.panel = _Panel(self.item, visible=panel_visible)
        self.editor = _Locator()
        self.keyboard = _Keyboard()
        self.wait_calls = []
        self.confirmation = {
            "source": "dom",
            "reason": "new_outgoing_sticker",
            "messageId": "native-message-1",
        }

    async def evaluate(self, *_args):
        return [{"key": "old-message"}]

    def locator(self, selector):
        if "emojiPanel" in selector or "EmojiPanel" in selector or "stickerPanel" in selector or "StickerPanel" in selector:
            return self.panel
        if "messageMsgInputiconAction" in selector or "aria-label" in selector or "role=\"button\"" in selector:
            return self.button
        return self.editor

    async def wait_for_timeout(self, milliseconds):
        self.wait_calls.append(milliseconds)

    async def wait_for_function(self, *_args, **_kwargs):
        return self.confirmation


class NativeStickerSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_panel_can_be_retried_without_submitting(self):
        page = _Page(panel_visible=False)
        checkpoint = []

        with self.assertRaises(NativeStickerPreparationError) as caught:
            await send_douyin_sticker(
                page, on_submitted=lambda: checkpoint.append("submitted"), timeout=1
            )

        self.assertFalse(caught.exception.submitted)
        self.assertEqual([], checkpoint)

    async def test_item_click_failure_does_not_mark_submitted(self):
        page = _Page(item_error=RuntimeError("panel detached"))
        checkpoint = []

        with self.assertRaises(NativeStickerPreparationError) as caught:
            await send_douyin_sticker(
                page, on_submitted=lambda: checkpoint.append("submitted")
            )

        self.assertFalse(caught.exception.submitted)
        self.assertEqual([], checkpoint)
        self.assertEqual(1, page.item.clicks)

    async def test_checkpoint_is_before_keyboard_send_and_confirmation_is_returned(self):
        page = _Page()
        order = []

        async def checkpoint():
            order.append("checkpoint")

        original_press = page.editor.press

        async def press(key):
            order.append(key)
            await original_press(key)

        page.editor.press = press
        result = await send_douyin_sticker(page, on_submitted=checkpoint)

        self.assertIsInstance(result, NativeStickerSendResult)
        self.assertTrue(result.submitted)
        self.assertTrue(result.confirmed)
        self.assertEqual("native-message-1", result.message_id)
        self.assertEqual(["checkpoint", "Enter"], order)
        self.assertEqual(["Escape"], page.keyboard.pressed)


if __name__ == "__main__":
    unittest.main()
