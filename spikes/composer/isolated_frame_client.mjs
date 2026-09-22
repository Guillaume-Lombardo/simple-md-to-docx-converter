const { PptxViewer, RECOMMENDED_ZIP_LIMITS } = globalThis.ComposerPptx;

const expectedParentOrigin = new URL(window.location.href).origin;
let newestRevision = 0;
let acceptedToken = "";
let blockedLinks = 0;

document.addEventListener(
  "click",
  (event) => {
    if (!(event.target instanceof Element) || !event.target.closest("a[href]"))
      return;
    event.preventDefault();
    event.stopImmediatePropagation();
    blockedLinks++;
  },
  true,
);
document.addEventListener("submit", (event) => event.preventDefault(), true);

window.addEventListener("message", async (event) => {
  if (event.source !== window.parent || event.origin !== expectedParentOrigin)
    return;
  const message = event.data;
  if (
    message?.type !== "render" ||
    typeof message.token !== "string" ||
    !Number.isSafeInteger(message.revision)
  )
    return;
  if (acceptedToken && message.token !== acceptedToken) return;
  if (message.revision <= newestRevision) return;
  acceptedToken = message.token;
  newestRevision = message.revision;
  if (message.delayMs) {
    await new Promise((done) => setTimeout(done, message.delayMs));
    if (message.revision < newestRevision) {
      window.parent.postMessage(
        {
          type: "result",
          token: message.token,
          revision: message.revision,
          result: { stale_probe: true },
        },
        expectedParentOrigin,
      );
      return;
    }
  }

  const result = {};
  const heapBefore = performance.memory?.usedJSHeapSize ?? null;
  let sampledHeapHighWater = heapBefore;
  const sampler = setInterval(() => {
    const current = performance.memory?.usedJSHeapSize;
    if (current != null)
      sampledHeapHighWater = Math.max(sampledHeapHighWater ?? 0, current);
  }, 5);
  try {
    window.parent.document.body;
    result.parent_dom_blocked = false;
  } catch {
    result.parent_dom_blocked = true;
  }
  try {
    await fetch("/fixture/docx");
    result.frame_document_fetch_blocked = false;
  } catch {
    result.frame_document_fetch_blocked = true;
  }
  try {
    await fetch("https://example.invalid/document");
    result.external_fetch_blocked = false;
  } catch {
    result.external_fetch_blocked = true;
  }

  try {
    const target = document.querySelector("#docx");
    await window.docx.renderAsync(message.documentBytes, target, undefined, {
      useBase64URL: true,
      renderAltChunks: false,
    });
    const links = [...target.querySelectorAll("a[href]")];
    const destinations = links.map(
      (link) => new URL(link.href, location.href).origin,
    );
    const locationBefore = location.href;
    links.forEach((link) => link.click());
    result.docx = {
      text_present: target.textContent.includes("Fixture Document"),
      section_width:
        target.querySelector("section")?.getBoundingClientRect().width ?? 0,
      document_links: links.length,
      same_origin_links: destinations.filter(
        (origin) => origin === expectedParentOrigin,
      ).length,
      external_origin_links: destinations.filter(
        (origin) => origin !== expectedParentOrigin,
      ).length,
      clicks_blocked:
        links.length > 0 &&
        blockedLinks === links.length &&
        location.href === locationBefore,
    };
  } catch (error) {
    result.docx = { error: String(error) };
  }

  try {
    const target = document.querySelector("#pptx");
    const viewer = await PptxViewer.open(message.presentationBytes, target, {
      zipLimits: RECOMMENDED_ZIP_LIMITS,
      lazySlides: true,
      lazyMedia: true,
      listOptions: { windowed: true, initialSlides: 2, batchSize: 2 },
    });
    result.pptx = {
      slides: viewer.slideCount,
      text_present: target.textContent.includes("Deck Title Slide"),
    };
  } catch (error) {
    result.pptx = { error: String(error) };
  }

  try {
    const started = performance.now();
    const target = document.querySelector("#docx-long");
    await window.docx.renderAsync(
      message.longDocumentBytes,
      target,
      undefined,
      {
        useBase64URL: true,
        renderAltChunks: false,
      },
    );
    result.long_docx = {
      bytes: message.longDocumentBytes.byteLength,
      full_parse_and_dom_ms: Math.round(performance.now() - started),
      sections: target.querySelectorAll("section").length,
      paragraph_nodes: target.querySelectorAll("p").length,
    };
  } catch (error) {
    result.long_docx = { error: String(error) };
  }

  try {
    const started = performance.now();
    const target = document.querySelector("#pptx-long");
    const viewer = await PptxViewer.open(
      message.longPresentationBytes,
      target,
      {
        zipLimits: RECOMMENDED_ZIP_LIMITS,
        lazySlides: true,
        lazyMedia: true,
        listOptions: { windowed: true, initialSlides: 2, batchSize: 2 },
      },
    );
    const initialMountMs = Math.round(performance.now() - started);
    const initialTextCount = (
      target.textContent.match(/Measured text\./g) ?? []
    ).length;
    const distantSlide = target.querySelector('[data-slide-index="60"]');
    distantSlide?.scrollIntoView();
    await new Promise((done) => setTimeout(done, 300));
    result.long_pptx = {
      bytes: message.longPresentationBytes.byteLength,
      archive_load_and_initial_mount_ms: initialMountMs,
      slides: viewer.slideCount,
      slide_slots: target.querySelectorAll("[data-slide-index]").length,
      initially_materialized_text_fragments: initialTextCount,
      distant_slide_text_present:
        distantSlide?.textContent.includes("Slide 60") ?? false,
      materialized_text_fragments_after_scroll: (
        target.textContent.match(/Measured text\./g) ?? []
      ).length,
    };
  } catch (error) {
    result.long_pptx = { error: String(error) };
  }

  const hostile = document.createElement("div");
  hostile.innerHTML =
    '<img src="https://example.invalid/remote.png" onerror="window.__documentCodeRan=true"><img src="/fixture/docx"><script>window.__documentCodeRan=true</script>';
  document.body.appendChild(hostile);
  const hostileStyle = document.createElement("style");
  hostileStyle.textContent =
    '@import url("https://example.invalid/remote.css"); body { background-image: url("https://example.invalid/background.png") }';
  document.head.appendChild(hostileStyle);
  await new Promise((done) => setTimeout(done, 25));
  result.hostile_dom = {
    script_or_handler_executed: window.__documentCodeRan === true,
    attempted_image_nodes: hostile.querySelectorAll("img").length,
    attempted_external_style: true,
  };
  clearInterval(sampler);
  result.memory = {
    heap_before_bytes: heapBefore,
    heap_after_bytes: performance.memory?.usedJSHeapSize ?? null,
    sampled_heap_high_water_bytes: sampledHeapHighWater,
  };

  window.parent.postMessage(
    {
      type: "result",
      token: message.token,
      revision: message.revision,
      result,
    },
    expectedParentOrigin,
  );
  window.parent.postMessage(
    {
      type: "result",
      token: "forged-token",
      revision: message.revision,
      result: { forged: true },
    },
    expectedParentOrigin,
  );
  window.parent.postMessage(
    {
      type: "result",
      token: message.token,
      revision: 1,
      result: { stale: true },
    },
    expectedParentOrigin,
  );
});

window.parent.postMessage({ type: "ready" }, expectedParentOrigin);
