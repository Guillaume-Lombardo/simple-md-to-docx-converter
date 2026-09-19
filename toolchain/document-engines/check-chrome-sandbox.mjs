import puppeteer from "./node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js";

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "shell",
});

try {
  const page = await browser.newPage();
  await page.goto("chrome://sandbox");
  const report = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll("tr"), (row) =>
      Array.from(row.querySelectorAll("td"), (cell) => cell.innerText.trim()),
    );
    return {
      body: document.body.innerText,
      statuses: Object.fromEntries(rows.filter((row) => row.length === 2)),
    };
  });

  const expected = {
    "Layer 1 Sandbox": "Namespace",
    "PID namespaces": "Yes",
    "Network namespaces": "Yes",
    "Seccomp-BPF sandbox": "Yes",
    "Seccomp-BPF sandbox supports TSYNC": "Yes",
  };
  for (const [name, value] of Object.entries(expected)) {
    if (report.statuses[name] !== value) {
      throw new Error(
        `Chrome sandbox diagnostic ${JSON.stringify(name)} was ${JSON.stringify(report.statuses[name])}, expected ${JSON.stringify(value)}`,
      );
    }
  }
  if (!report.body.includes("You are adequately sandboxed.")) {
    throw new Error("Chrome did not report that the browser is adequately sandboxed");
  }
} finally {
  await browser.close();
}

console.log("chrome_sandbox=passed");
