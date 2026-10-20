import unittest
from nightcloser import twilio_wa

URL, TOK = "https://example.test/twilio", "tok"


class Fake:
    def reply(self, sid, text):
        self.last = (sid, text)
        return {"text": "Hi <b> & co", "handoff": False}


def signed(body):
    from urllib.parse import parse_qsl
    return twilio_wa.signature(URL, parse_qsl(body), TOK)


class T(unittest.TestCase):
    def test_bad_signature_403_and_no_reply(self):
        f = Fake()
        self.assertEqual(twilio_wa.handle(f, b"From=whatsapp%3A%2B1&Body=hi", "bad", URL, TOK), (403, ""))
        self.assertFalse(hasattr(f, "last"))

    def test_missing_token_403(self):
        self.assertEqual(twilio_wa.handle(Fake(), b"Body=hi", signed("Body=hi"), URL, "")[0], 403)

    def test_ok_reply_escaped_twiml(self):
        f, b = Fake(), "From=whatsapp%3A%2B421900&Body=ahoj"
        code, xml = twilio_wa.handle(f, b.encode(), signed(b), URL, TOK)
        self.assertEqual(code, 200)
        self.assertEqual(f.last, ("wa:+421900", "ahoj"))
        self.assertEqual(xml, "<Response><Message>Hi &lt;b&gt; &amp; co</Message></Response>")

    def test_non_whatsapp_or_empty_is_noop(self):
        f, b = Fake(), "From=%2B421900&Body=x"
        self.assertEqual(twilio_wa.handle(f, b.encode(), signed(b), URL, TOK), (200, "<Response/>"))
        self.assertFalse(hasattr(f, "last"))


if __name__ == "__main__":
    unittest.main()
