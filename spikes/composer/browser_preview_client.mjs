import { PptxViewer, RECOMMENDED_ZIP_LIMITS } from "/vendor/pptx.browser.mjs";
import * as pdfjs from "/vendor/pdf.mjs";

const result = {};
const bytes = async (path) =>
  new Uint8Array(await (await fetch(path)).arrayBuffer());

try {
  const target = document.querySelector("#docx");
  await window.docx.renderAsync(
    await bytes("/fixture/docx"),
    target,
    undefined,
    {
      useBase64URL: true,
    },
  );
  result.docx = {
    sections: target.querySelectorAll("section").length,
    text_present: target.textContent.includes("Fixture Document"),
    first_section_width:
      target.querySelector("section")?.getBoundingClientRect().width ?? 0,
  };
} catch (error) {
  result.docx = { error: String(error) };
}

try {
  const target = document.querySelector("#docx-break");
  await window.docx.renderAsync(
    await bytes("/fixture/break.docx"),
    target,
    undefined,
    {
      useBase64URL: true,
    },
  );
  result.docx_explicit_break = {
    sections: target.querySelectorAll("section").length,
  };
} catch (error) {
  result.docx_explicit_break = { error: String(error) };
}

try {
  const viewer = await PptxViewer.open(
    await bytes("/fixture/pptx"),
    document.querySelector("#pptx"),
    {
      zipLimits: RECOMMENDED_ZIP_LIMITS,
      lazySlides: true,
      lazyMedia: true,
      listOptions: { windowed: true, initialSlides: 1, batchSize: 1 },
    },
  );
  result.pptx = {
    slides: viewer.slideCount,
    slide_slots: document.querySelectorAll("[data-slide-index]").length,
    text_present: document
      .querySelector("#pptx")
      .textContent.includes("Deck Title Slide"),
    container_width: document.querySelector("#pptx").getBoundingClientRect()
      .width,
    first_slot_width:
      document
        .querySelector("#pptx [data-slide-index]")
        ?.getBoundingClientRect().width ?? 0,
  };
} catch (error) {
  result.pptx = { error: String(error) };
}

try {
  pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.mjs";
  const task = pdfjs.getDocument({ data: await bytes("/fixture/pdf") });
  const document = await task.promise;
  const page = await document.getPage(1);
  const canvas = window.document.querySelector("#pdf");
  const viewport = page.getViewport({ scale: 1 });
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  await page.render({ canvasContext: canvas.getContext("2d"), viewport })
    .promise;
  result.pdf = { pages: document.numPages, canvas_width: canvas.width };
  await task.destroy();
} catch (error) {
  result.pdf = { error: String(error) };
}

try {
  const started = performance.now();
  const target = document.querySelector("#docx-long");
  await window.docx.renderAsync(
    await bytes("/fixture/long.docx"),
    target,
    undefined,
    {
      useBase64URL: true,
    },
  );
  result.long_docx = {
    elapsed_ms: Math.round(performance.now() - started),
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
    await bytes("/fixture/long.pptx"),
    target,
    {
      zipLimits: RECOMMENDED_ZIP_LIMITS,
      lazySlides: true,
      lazyMedia: true,
      listOptions: { windowed: true, initialSlides: 2, batchSize: 2 },
    },
  );
  result.long_pptx = {
    elapsed_ms: Math.round(performance.now() - started),
    slides: viewer.slideCount,
    slide_slots: target.querySelectorAll("[data-slide-index]").length,
    materialized_text_count: (
      target.textContent.match(/Measured text\./g) ?? []
    ).length,
  };
} catch (error) {
  result.long_pptx = { error: String(error) };
}

try {
  const started = performance.now();
  pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.mjs";
  const task = pdfjs.getDocument({ data: await bytes("/fixture/long.pdf") });
  const document = await task.promise;
  const page = await document.getPage(50);
  const canvas = window.document.querySelector("#pdf-long");
  const viewport = page.getViewport({ scale: 1 });
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  await page.render({ canvasContext: canvas.getContext("2d"), viewport })
    .promise;
  result.long_pdf = {
    elapsed_ms: Math.round(performance.now() - started),
    pages: document.numPages,
    canvas_width: canvas.width,
  };
  await task.destroy();
} catch (error) {
  result.long_pdf = { error: String(error) };
}

window.__probe = result;
