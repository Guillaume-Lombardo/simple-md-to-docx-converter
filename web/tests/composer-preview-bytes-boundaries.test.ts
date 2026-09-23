import {
  readBoundedResponse,
  verifySha256,
} from "../src/composer/preview/bytes";

const officeType =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

test("a forged content length is rejected before consuming revision bytes", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array([1, 2, 3]));
      controller.close();
    },
  });
  const response = new Response(body, {
    headers: { "Content-Type": officeType, "Content-Length": "99" },
  });
  await expect(readBoundedResponse(response, 3, officeType)).rejects.toThrow(
    "configured size limit",
  );
  expect(response.bodyUsed).toBe(false);
});

test("an empty artifact body cannot be treated as a usable Office preview", async () => {
  const response = new Response(null, {
    headers: { "Content-Type": officeType },
  });
  await expect(readBoundedResponse(response, 3, officeType)).rejects.toThrow(
    "response is empty",
  );
});

test("invalid digest metadata is rejected before using revision bytes", async () => {
  const bytes = new Uint8Array([1, 2, 3]).buffer;
  await expect(verifySha256(bytes, "not-a-sha256-digest")).rejects.toThrow(
    "Invalid preview digest",
  );
});
