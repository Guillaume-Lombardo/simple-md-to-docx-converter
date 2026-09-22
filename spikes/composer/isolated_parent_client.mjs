const frame = document.querySelector("#preview");
const token = crypto.randomUUID();
const revision = 2;
const protocol = {
  accepted: 0,
  rejected_source: 0,
  rejected_token: 0,
  rejected_revision: 0,
};
let acceptedResult = null;

window.addEventListener("message", async (event) => {
  if (event.source !== frame.contentWindow || event.origin !== "null") {
    if (event.data?.type === "ready" || event.data?.type === "result")
      protocol.rejected_source++;
    return;
  }
  if (event.data?.type === "ready") {
    const fetchBytes = async (path) => (await fetch(path)).arrayBuffer();
    const [
      documentBytes,
      presentationBytes,
      longDocumentBytes,
      longPresentationBytes,
    ] = await Promise.all([
      fetchBytes("/fixture/docx"),
      fetchBytes("/fixture/pptx"),
      fetchBytes("/fixture/long.docx"),
      fetchBytes("/fixture/long.pptx"),
    ]);
    frame.contentWindow.postMessage(
      { type: "render", token, revision: 1, delayMs: 150 },
      "*",
    );
    frame.contentWindow.postMessage(
      {
        type: "render",
        token,
        revision,
        documentBytes,
        presentationBytes,
        longDocumentBytes,
        longPresentationBytes,
      },
      "*",
      [
        documentBytes,
        presentationBytes,
        longDocumentBytes,
        longPresentationBytes,
      ],
    );
    return;
  }
  if (event.data?.type !== "result") return;
  if (event.data.token !== token) {
    protocol.rejected_token++;
    return;
  }
  if (event.data.revision !== revision) {
    protocol.rejected_revision++;
    return;
  }
  protocol.accepted++;
  acceptedResult = event.data.result;
  setTimeout(() => {
    window.__isolatedProbe = { ...acceptedResult, protocol };
  }, 250);
});
