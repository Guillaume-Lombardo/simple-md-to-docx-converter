import { randomBytes } from "node:crypto";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(): Response {
  const nonce = randomBytes(18).toString("base64");
  const policy = [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "sandbox allow-scripts",
    "frame-src 'none'",
    "form-action 'none'",
    `script-src 'nonce-${nonce}'`,
    "style-src 'unsafe-inline'",
    "connect-src 'none'",
    "img-src data: blob:",
    "font-src data: blob:",
    "worker-src 'none'",
    "manifest-src 'none'",
  ].join("; ");
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Document preview</title><style>
    html,body{margin:0;background:#f4f5f7;color:#1d2838;font-family:system-ui,sans-serif}
    #viewport{height:100vh;overflow:auto;overscroll-behavior:contain}
    #document{width:max-content;min-width:100%;margin:0 auto;padding:20px;box-sizing:border-box}
    #document a{pointer-events:none;text-decoration:none;color:inherit}
    #document img{max-width:100%}
    #slide-controls{display:none;align-items:center;justify-content:center;gap:18px;padding:12px}
    #slide-controls button{border:1px solid #8d99aa;border-radius:5px;background:#fff;padding:5px 12px;cursor:pointer}
    #slide-controls button:disabled{opacity:.5;cursor:default}
    #slide-thumbnails{display:none;gap:10px;overflow:auto;padding:12px;justify-content:center}
    .slide-thumbnail{width:138px;height:94px;flex:none;overflow:hidden;border:1px solid #8d99aa;border-radius:5px;background:#fff;padding:3px;text-align:left;cursor:pointer}
    .slide-thumbnail[aria-current="page"]{outline:2px solid #315eaa}
    .thumbnail-render{width:1058px;height:596px;transform:scale(.12);transform-origin:top left;pointer-events:none}
    .preview-error{padding:24px;max-width:42rem;margin:auto}
  </style></head><body><main id="viewport"><div id="document"></div><nav id="slide-controls" aria-label="Presentation slides"><button id="previous-slide" type="button">Previous slide</button><span id="slide-position"></span><button id="next-slide" type="button">Next slide</button></nav><nav id="slide-thumbnails" aria-label="Nearby slide thumbnails"></nav></main>
    <script nonce="${nonce}" src="/composer-preview/jszip.js"></script>
    <script nonce="${nonce}" src="/composer-preview/docx.js"></script>
    <script nonce="${nonce}" src="/composer-preview/pptx.js"></script>
    <script nonce="${nonce}" src="/composer-preview/frame-client.js"></script>
  </body></html>`;
  return new Response(html, {
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy": policy,
      "Content-Type": "text/html; charset=utf-8",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
