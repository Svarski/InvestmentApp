# ✅ COMMIT CHECKLIST – Investment App

---

# 🎯 PURPOSE

Ensure every change is safe, stable, and documented.

---

# 🧠 1. LOGIC CHECK

* [ ] Does the feature work as expected?
* [ ] Did I change existing behavior unintentionally?
* [ ] Is the system still deterministic?

---

# ⚙️ 2. WORKER SAFETY

* [ ] Worker still runs without crashing
* [ ] No blocking code added
* [ ] All new logic wrapped in try/except

---

# 🚨 3. ALERT SYSTEM

* [ ] Alerts still deduplicated
* [ ] No spam risk introduced
* [ ] Threshold logic unchanged (unless intentional)

---

# 📣 4. NOTIFIER

* [ ] Alerts still go through MultiNotifier
* [ ] ALERT_CHANNEL respected
* [ ] No direct send bypass

---

# 🗄 5. DATABASE (IF USED)

* [ ] No schema break
* [ ] Queries simple
* [ ] DB failure does not crash worker

---

# ⚙️ 6. CONFIG (.env)

* [ ] No hardcoded values added
* [ ] All config via os.getenv
* [ ] Defaults are safe (no secrets)

👉 config mora biti izključno .env 

---

# 🖥 7. UI

* [ ] No business logic added to UI
* [ ] Only display / formatting changes

---

# 📄 8. DOCUMENTATION (CRITICAL)

* [ ] system_overview.md updated (if needed)
* [ ] module docs updated
* [ ] ai_rules.md updated (if rules changed)

👉 dokumentacija mora biti vedno usklajena s kodo 

---

# 🧪 9. RUNTIME CHECK

* [ ] App starts without errors
* [ ] Worker runs
* [ ] Logs look clean

---

# 🚀 10. DEPLOY READINESS

* [ ] Safe to deploy?
* [ ] No debug code left
* [ ] No secrets exposed

Deploy flow:

```bash
git push
→ server
git pull
docker-compose up -d --build
```

👉 standard workflow 

---

# 🔥 FINAL RULE

If ANY doubt:

❌ do not commit
✔ test again or ask

---
