/**
 * apps_script_requests.gs — 接收前端「看票申请」,写入 requests 工作表
 * ===========================================================================
 * 这是 Google Apps Script,不是本地 Python。一次性部署步骤:
 *   1. 打开你的「A股价值投资系统」Google 表格
 *   2. 扩展程序(Extensions) → Apps Script
 *   3. 把本文件内容整体粘贴进去,保存
 *   4. 右上「部署 Deploy」→「新建部署 New deployment」→ 类型选「网络应用 Web app」
 *        - 说明:requests intake
 *        - 执行身份 Execute as:我自己 Me
 *        - 谁有权访问 Who has access:任何人 Anyone
 *   5. 部署后复制 Web App URL(形如 https://script.google.com/macros/s/XXXX/exec)
 *   6. 把该 URL 填到 index.html 顶部的 REQUEST_ENDPOINT 常量
 *
 * 安全说明:该 URL 是公开的(任何人知道即可提交)。因为只面向你认识的几个人,
 * 建议给 Cloudflare Pages 站点套上 Cloudflare Access(免费,按邮箱白名单),
 * 这样只有受邀的人能打开页面、看到提交框。服务端这里也做了去重与6位校验。
 */
var SHEET_NAME = 'requests';
var HEADERS = ['code', 'note', 'requested_by', 'time', 'status'];

function doPost(e) { return handle_(e); }
function doGet(e)  { return handle_(e); }

function handle_(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(5000);                                   // 防并发写串行化
  try {
    var p = (e && e.parameter) || {};
    var code = String(p.code || '').replace(/\D/g, '').slice(0, 6);
    if (code.length !== 6) {
      return json_({ ok: false, error: 'code 必须是6位数字' });
    }

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName(SHEET_NAME);
    if (!sh) {                                           // 首次自动建表+表头
      sh = ss.insertSheet(SHEET_NAME);
      sh.appendRow(HEADERS);
    }

    // 去重:同一 code 已有 pending 行,则不重复入队
    var data = sh.getDataRange().getValues();
    for (var i = 1; i < data.length; i++) {
      if (String(data[i][0]) === code && String(data[i][4]).toLowerCase() === 'pending') {
        return json_({ ok: true, status: 'already_pending', code: code });
      }
    }

    sh.appendRow([
      code,
      String(p.note || '').slice(0, 200),
      String(p.requested_by || '').slice(0, 80),
      new Date().toISOString(),
      'pending'
    ]);
    return json_({ ok: true, status: 'queued', code: code });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
