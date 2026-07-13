# Project Guidelines

## Technical Decision Making

When recommending approaches, prioritise in this order:

1. **First-party provider solutions** — Anthropic, OpenAI, Google native platform features and SDKs
2. **Established frameworks** — LangChain, LlamaIndex, Semantic Kernel, etc.
3. **Industry practice** — well-documented patterns from engineering blogs, production case studies
4. **Academic papers** — background context only, never the primary justification for a design choice

Academic benchmark numbers are not trusted as a basis for engineering decisions. They optimise for controlled settings, not real-world constraints.

## Wiki Schema（wiki/ 目錄）

`wiki/` 是本 repo 的 LLM wiki（Karpathy 模式）：agent 維護的知識層，記錄程式碼讀不出來的
設計理由、決策、地雷。**改架構、換服務、加功能之前，先讀 `wiki/index.md` 找到相關頁面。**

慣例：
- `index.md` = 目錄（每頁一行摘要）；`log.md` = append-only 時間軸，
  格式 `## [YYYY-MM-DD] <type> | <title>`，type ∈ ingest / change / lint / decision
- 頁面間用 `[[頁名]]` 交叉連結；頁首 frontmatter 記 `updated` 與 `verified-against`
- 內容原則：只寫程式碼讀不出來的東西（理由、取捨、被否決的方案、調參位置、教訓）

工作流程（由 agent 執行，人不直接寫頁面）：
- **Ingest**：每次有意義的變更（新功能、架構調整、重要 bug 教訓、新決策）後，
  更新受影響的頁面 + 追加一筆 log。決策變更同步更新 `decisions.md` 的前提註記。
- **Query**：回答架構/設計問題時先查 wiki 再讀碼；wiki 與程式碼矛盾時以程式碼為準，
  並修正 wiki。
- **Lint**（偶爾，或使用者要求時）：檢查過期宣稱（對照程式碼現況）、孤兒頁、
  缺漏的交叉連結、log 與頁面的不一致；完成後記一筆 `lint` log。
