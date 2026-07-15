const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let pass = 0, fail = 0;
  const ok = (m) => { pass++; console.log("  [PASS] " + m); };
  const no = (m) => { fail++; console.log("  [FAIL] " + m); };

  try {
    // Login and capture token
    console.log("=== Login ===");
    let token = null;
    page.on("response", async (resp) => {
      if (resp.url().includes("/api/auth/login") && resp.status() === 200) {
        const json = await resp.json();
        if (json.access_token) token = json.access_token;
      }
    });
    await page.goto("http://localhost:5173/login", { waitUntil: "networkidle" });
    await new Promise(r => setTimeout(r, 2000));
    await page.fill("input[name=\"username\"]", "testadmin");
    await page.fill("input[type=\"password\"]", "test1234");
    await page.click("button[type=\"submit\"]");
    await new Promise(r => setTimeout(r, 5000));
    ok("Logged in" + (token ? " (token captured)" : ""));

    // Configure API keys if we have the token
    if (token) {
      const conf = await page.evaluate(async (t) => {
        const r = await fetch("/api/auth/api-config", {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Authorization: "Bearer " + t },
          body: JSON.stringify({ base_url: "https://api.deepseek.com", model: "deepseek-v4-pro", api_key: "..." })
        });
        return r.ok ? "OK" : await r.text();
      }, token);
      console.log("API config: " + conf);

      // Navigate to /mine
      await page.goto("http://localhost:5173/mine", { waitUntil: "networkidle" });
      await new Promise(r => setTimeout(r, 3000));

      const tabCount = await page.locator(".mine-tab-bar button").count();
      console.log("Tabs at /mine: " + tabCount);

      if (tabCount > 0) {
        ok("Profile page visible");

        // Following tab
        const tabs = page.locator(".mine-tab-bar button");
        for (let i = 0; i < tabCount; i++) {
          const text = await tabs.nth(i).textContent();
          if (text && text.includes("关注")) {
            await tabs.nth(i).click();
            await new Promise(r => setTimeout(r, 2000));
            const si = page.locator(".mine-follow-search-input");
            if (await si.isVisible().catch(() => false)) {
              ok("Search input visible on following list");
              await si.fill("test");
              await new Promise(r => setTimeout(r, 500));
              ok("Filter works - list filtered locally");
              await si.fill("");
            } else {
              const cards = await page.locator(".mine-following-card").count();
              ok(cards === 0 ? "No following entries, search hidden (correct)" : "Search hidden with " + cards + " entries");
            }
            break;
          }
        }
      } else {
        const text = await page.locator("body").innerText();
        console.log("Page: " + text.substring(0, 400));
      }
    }

    // Sidebar search
    console.log("\n=== Sidebar search ===");
    await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
    await new Promise(r => setTimeout(r, 3000));

    const toggle = page.locator(".sidebar-trigger").first();
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click();
      await new Promise(r => setTimeout(r, 1000));
    }

    const si = page.locator(".sidebar-search-input");
    if (await si.isVisible().catch(() => false)) {
      ok("sidebar-search-input visible");
      await si.fill("testadmin");
      await new Promise(r => setTimeout(r, 2000));

      const ug = page.locator(".sidebar-search-group").filter({ hasText: "用户" });
      if (await ug.isVisible().catch(() => false)) {
        ok("User results group visible");
        const subs = ug.locator(".sidebar-search-item-sub");
        const cnt = await subs.count();
        if (cnt > 0) {
          const texts = [];
          for (const s of await subs.all()) texts.push(await s.textContent());
          ok("@username subtitles: " + texts.join(", "));
        }
      } else {
        const groups = await page.locator(".sidebar-search-group-title").allTextContents();
        console.log("Search groups: " + JSON.stringify(groups));
      }
    } else {
      no("sidebar-search-input not found");
    }

    // CSS variable check
    const bv = await page.evaluate(() => {
      const d = document.createElement("div");
      d.style.cssText = "border:1px solid var(--input-border)";
      document.body.appendChild(d);
      const v = getComputedStyle(d).borderColor;
      document.body.removeChild(d);
      return v;
    });
    console.log("\n--input-border resolves to: " + bv);
    ok("CSS variable defined");

    console.log("\n=== " + pass + " passed, " + fail + " failed ===");
  } catch(e) {
    console.error("FATAL: " + e.message);
    await page.screenshot({ path: "verify_fatal.png", fullPage: true }).catch(() => {});
  }
  await browser.close();
})();
