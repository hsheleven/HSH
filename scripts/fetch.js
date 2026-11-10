#!/usr/bin/env node
/**
 * 复盘工作台 · 抓取脚本
 * 抓指定交易日（默认今天）的同花顺涨停池 + 跌停池，落到 data/raw/YYYYMMDD.json
 *
 * 用法:
 *   node fetch.js              抓今天
 *   node fetch.js 20261106     抓指定日
 *   node fetch.js --backfill 30  从 N 天前补到今天
 *
 * 退出码: 0=有数据 / 2=空(非交易日) / 1=失败
 */
const fs = require("fs");
const path = require("path");

const BASE = path.dirname(path.dirname(path.resolve(__filename)));
const RAW = path.join(BASE, "data", "raw");
const FIELD = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36";
const HEADERS = {
  "User-Agent": UA,
  "Referer": "https://data.10jqka.com.cn/",
  "Accept": "application/json, text/javascript, */*; q=0.01",
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

function shiftDays(s, n) {
  const d = new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

async function fetchPool(kind, date) {
  const base = kind === "zt" ? "limit_up_pool" : "lower_limit_pool";
  let all = [];
  let total = 0;
  for (let page = 1; page <= 5; page++) {
    const url =
      `https://data.10jqka.com.cn/dataapi/limit_up/${base}` +
      `?page=${page}&limit=200&field=${FIELD}&filter=HS,GEM2STAR` +
      `&order_field=330324&order_type=0&date=${date}`;
    let j = null;
    for (let i = 0; i < 4; i++) {
      try {
        const r = await fetch(url, { headers: HEADERS });
        j = await r.json();
        if (j && j.status_code === 0) break;
      } catch (e) {
        j = { status_code: -999, status_msg: e.message };
      }
      await sleep(2500 * (i + 1));
    }
    if (!j || j.status_code !== 0) {
      throw new Error(`${kind} ${date} page${page} failed: ${j && (j.status_msg || j.status_code)}`);
    }
    total = (j.data && j.data.page && j.data.page.total) || 0;
    const info = (j.data && j.data.info) || [];
    all = all.concat(info);
    if (all.length >= total || info.length < 200) break;
    await sleep(1000);
  }
  return { total, list: all };
}

function slimZT(x) {
  return {
    code: x.code, name: x.name, latest: x.latest,
    change_rate: x.change_rate, reason: x.reason_type,
    high_days: x.high_days, high_days_value: x.high_days_value,
    limit_up_type: x.limit_up_type, open_num: x.open_num,
    order_amount: x.order_amount, order_volume: x.order_volume,
    limit_up_suc_rate: x.limit_up_suc_rate, is_again_limit: x.is_again_limit,
    first_time: x.first_limit_up_time, last_time: x.last_limit_up_time,
    turnover_rate: x.turnover_rate, currency_value: x.currency_value,
    market_id: x.market_id,
  };
}

function slimDT(x) {
  return {
    code: x.code, name: x.name, latest: x.latest,
    change_rate: x.change_rate, reason: x.reason_type,
    high_days: x.high_days, open_num: x.open_num,
    first_time: x.first_limit_up_time, last_time: x.last_limit_up_time,
    turnover_rate: x.turnover_rate,
  };
}

async function grabOne(date, { force = false } = {}) {
  const outFile = path.join(RAW, `${date}.json`);
  if (!force && fs.existsSync(outFile)) {
    const old = JSON.parse(fs.readFileSync(outFile, "utf8"));
    if ((old.zt_total || 0) > 0) {
      console.log(`${date} 已存在（涨停${old.zt_total}），跳过`);
      return { date, skipped: true, zt: old.zt_total };
    }
  }

  const zt = await fetchPool("zt", date);
  await sleep(1100);
  const dt = await fetchPool("dt", date);

  if (zt.total === 0 && dt.total === 0) {
    console.log(`${date} 无数据（非交易日或未开盘）`);
    return { date, empty: true };
  }

  const rec = {
    date,
    fetched_at: new Date().toISOString(),
    zt_total: zt.total,
    dt_total: dt.total,
    zhangting: zt.list.map(slimZT),
    dieting: dt.list.map(slimDT),
  };
  fs.mkdirSync(RAW, { recursive: true });
  fs.writeFileSync(outFile, JSON.stringify(rec, null, 1), "utf8");
  console.log(`${date} 抓取完成：涨停 ${zt.total} / 跌停 ${dt.total} -> ${path.basename(outFile)}`);
  return { date, zt: zt.total, dt: dt.total };
}

async function main() {
  const args = process.argv.slice(2);
  fs.mkdirSync(RAW, { recursive: true });

  if (args[0] === "--backfill") {
    const n = parseInt(args[1] || "30", 10);
    const end = todayStr();
    let ok = 0, empty = 0, skip = 0, fail = 0;
    for (let i = n; i >= 0; i--) {
      const d = shiftDays(end, -i);
      const wd = new Date(`${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}T00:00:00Z`).getUTCDay();
      if (wd === 0 || wd === 6) continue;
      try {
        const r = await grabOne(d);
        if (r.skipped) skip++;
        else if (r.empty) empty++;
        else ok++;
      } catch (e) {
        fail++;
        console.error(`${d} 失败: ${e.message}`);
      }
      await sleep(1200);
    }
    console.log(`\n补抓完成 ok=${ok} 空=${empty} 跳过=${skip} 失败=${fail}`);
    process.exitCode = fail > 0 ? 1 : 0;
    return;
  }

  const date = args[0] && /^\d{8}$/.test(args[0]) ? args[0] : todayStr();
  const force = args.includes("--force");
  try {
    const r = await grabOne(date, { force });
    if (r.empty) process.exitCode = 2;
  } catch (e) {
    console.error(`${date} 抓取失败: ${e.message}`);
    process.exitCode = 1;
  }
}

main();
