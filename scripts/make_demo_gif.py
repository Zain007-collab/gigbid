"""Build the README demo GIF (~30 s) from the numbered walkthrough screenshots in docs/images/."""
from pathlib import Path

from PIL import Image, ImageDraw

IMG = Path(__file__).resolve().parents[1] / "docs" / "images"
FRAMES = [  # (screenshot, caption, seconds)
    ("04-job-inbox-ranked-by-fit", "Jobs ranked by fit, risk flagged, price and Connects suggested", 3.0),
    ("03-run-viewer-live-progress", "Agents run in parallel — live, node by node", 2.5),
    ("05-job-detail-evidence-matrix", "Every requirement mapped to portfolio evidence", 3.0),
    ("07-proposal-studio-evidence-on-hover", "Every claim shows the evidence behind it", 3.5),
    ("08-approval-dry-run-preview", "Dry-run preview: exactly what will be sent, and its Connects cost", 3.0),
    ("09-proposal-submitted-exactly-once", "Approved by a human — submitted exactly once", 2.5),
    ("10-scam-caught-and-skipped", "Scam caught: unpaid 'test' work, skipped with the reason", 3.0),
    ("11-prompt-injection-flagged", "Injected instruction flagged and ignored", 2.5),
    ("13-client-reply-suggested-response", "Client replied — a reply is drafted, waiting for approval", 3.0),
    ("15-pipeline-board", "Pipeline board updates from marketplace events", 2.5),
    ("19-degraded-mode-banner", "Marketplace down? The UI says so, work is queued and resumes", 2.5),
]
W = 1100


def frame(name: str, caption: str) -> Image.Image:
    im = Image.open(IMG / f"{name}.png").convert("RGB")
    im = im.resize((W, int(im.height * W / im.width)))
    bar = 46
    out = Image.new("RGB", (W, im.height + bar), (24, 24, 27))
    out.paste(im, (0, bar))
    ImageDraw.Draw(out).text((16, 14), caption, fill=(255, 255, 255))
    return out


frames = [frame(n, c) for n, c, _ in FRAMES]
frames = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
frames[0].save(IMG / "demo.gif", save_all=True, append_images=frames[1:], loop=0,
               duration=[int(s * 1000) for _, _, s in FRAMES], optimize=True)
print("wrote", IMG / "demo.gif", f"{(IMG / 'demo.gif').stat().st_size / 1e6:.1f} MB,", f"{sum(s for *_, s in FRAMES):.0f} s")
