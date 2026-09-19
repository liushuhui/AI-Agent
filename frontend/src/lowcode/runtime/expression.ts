/* eslint-disable @typescript-eslint/no-explicit-any -- 解释器内部到处传播动态值，类型无法静态描述 */
/**
 * 受限表达式引擎：`{{ ... }}` 模板 + 安全求值（不用 eval / new Function）。
 *
 * 支持：字面量、属性路径、数组下标、一元（! -）、算术（+ - * / %）、
 * 比较（> >= < <= == != === !==）、逻辑（&& ||）、三元（?:）、白名单函数调用。
 * 上下文（state / dataSources / env / record / event）由调用方注入。
 */

export type ExprScope = Record<string, any>;

/** 白名单函数：表达式里只能调这些，扩展时往这里加。 */
const HELPERS: Record<string, (...args: any[]) => any> = {
  /** 转数字：num("3.14") → 3.14 */
  num: (value) => Number(value),
  /** 转字符串：null / undefined 归为 ""，避免显示成 "null" 文本 */
  str: (value) => (value == null ? "" : String(value)),
  /** 取长度：数组取元素个数，其他值先转字符串再取字符数 */
  len: (value) => (Array.isArray(value) ? value.length : value == null ? 0 : String(value).length),
  /** 判空：null / undefined / 空串 / 空数组都算「空」 */
  empty: (value) => value == null || value === "" || (Array.isArray(value) && value.length === 0),
  /** 转大写字符串：upper("abc") → "ABC" */
  upper: (value) => String(value ?? "").toUpperCase(),
  /** 转小写字符串：lower("ABC") → "abc" */
  lower: (value) => String(value ?? "").toLowerCase(),
  /** 按模板格式化日期：formatDate("2024-01-02T10:20:30", "YYYY/MM/DD HH:mm")；非法日期返回 ""。可用占位符：YYYY MM DD HH mm ss */
  formatDate: (value, format: string = "YYYY-MM-DD") => {
    const date = new Date(value as string | number);
    if (Number.isNaN(date.getTime())) return ""; // 无效日期（如空串）直接返回空
    const pad = (n: number) => String(n).padStart(2, "0"); // 个位数补零：3 → "03"
    const map: Record<string, string> = {
      YYYY: String(date.getFullYear()),
      MM: pad(date.getMonth() + 1),
      DD: pad(date.getDate()),
      HH: pad(date.getHours()),
      mm: pad(date.getMinutes()),
      ss: pad(date.getSeconds()),
    };
    return format.replace(/YYYY|MM|DD|HH|mm|ss/g, (token) => map[token]);
  },
};

// ---------------- 词法 ----------------

/** 词法单元：t 是类别（num 数字 / str 字符串 / id 标识符 / op 操作符），v 是内容。 */
interface Token {
  t: "num" | "str" | "id" | "op";
  v: string;
}

/**
 * 全部操作符。顺序很重要：多字符的必须排在单字符前面
 * （tokenize 按数组顺序做前缀匹配，否则 "===" 会先被截成 "=="）。
 */
const OPS = [
  "===", "!==", ">=", "<=", "==", "!=", "&&", "||",
  "+", "-", "*", "/", "%", "!", "<", ">", "(", ")", "[", "]", ".", ",", "?", ":",
];
/** 
   词法分析器（Tokenizer / Lexer），
  作用是把一段源代码字符串拆分成一个个有意义的 Token（词法单元），为后续的语法分析（Parser）做准备。
  输入：source: string，例如 "a + 1 * 'hello'"。
  输出：Token[]，每个 Token 形如：
  { t: "num" | "str" | "id" | "op", v: string }
*/
function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    // 跳过空白字符
    if (/\s/.test(ch)) {
      i += 1;
      continue;
    }
    // 数字（num）：123 / 3.14 / .5（「.数字」也视为数字开头，和「.属性」区分开）
    if (/[0-9]/.test(ch) || (ch === "." && /[0-9]/.test(source[i + 1] ?? ""))) {
      // j 向后扫到第一个非数字/非点字符，[i, j) 即整个数字字面量
      let j = i + 1;
      while (j < source.length && /[0-9.]/.test(source[j])) j += 1;
      // v 先存原文，转 Number 放到语法分析阶段（parsePrimary）做
      tokens.push({ t: "num", v: source.slice(i, j) });
      i = j;
      continue;
    }
    // 字符串（str）：单引号 / 双引号都可以，引号内的「\」表示转义下一个字符
    if (ch === "'" || ch === '"') {
      let j = i + 1; // 跳过起始引号
      let value = ""; // 收集去掉引号、处理过转义后的内容
      while (j < source.length && source[j] !== ch) {
        if (source[j] === "\\") {
          j += 1; // 转义：跳过「\」本身，让下一个字符原样进入 value
          if (j >= source.length) break;
        }
        value += source[j];
        j += 1;
      }
      if (j >= source.length) throw new Error("字符串没有闭合"); // 扫到末尾也没遇到收尾引号
      tokens.push({ t: "str", v: value });
      i = j + 1; // 跳过收尾引号，从下一个字符继续
      continue;
    }
    // 标识符（id）：变量名、函数名；true / false / null / undefined 也先按 id 拆出来
    if (/[A-Za-z_$]/.test(ch)) {
      // 首字符是字母/_/$，后续可跟字母、数字、_、$
      let j = i + 1;
      while (j < source.length && /[\w$]/.test(source[j])) j += 1;
      tokens.push({ t: "id", v: source.slice(i, j) });
      i = j;
      continue;
    }
    // 操作符（op）：从当前下标开始做前缀匹配，取到最长的合法操作符
    const op = OPS.find((candidate) => source.startsWith(candidate, i));
    if (!op) throw new Error(`不支持的字符「${ch}」`);
    tokens.push({ t: "op", v: op });
    i += op.length; // 跳过的长度按实际操作符算，多字符不能只 +1
  }
  return tokens;
}

// ---------------- 语法（Pratt 解析器） ----------------

/** 语法树节点联合类型：k 标识节点种类，求值时按 k 分派（见 evalAst）。 */
type Ast =
  | { k: "lit"; v: any } // 字面量：数字 / 字符串 / true / false / null / undefined
  | { k: "id"; name: string } // 变量引用：求值时从 scope 按 name 取
  | { k: "un"; op: string; a: Ast } // 一元运算：!a 或 -a
  | { k: "bin"; op: string; a: Ast; b: Ast } // 二元运算：a + b、a && b 等
  | { k: "cond"; c: Ast; a: Ast; b: Ast } // 三元：c ? a : b
  | { k: "member"; obj: Ast; prop: string } // 属性访问：obj.prop
  | { k: "index"; obj: Ast; idx: Ast } // 下标访问：obj[idx]
  | { k: "call"; name: string; args: Ast[] }; // 白名单函数调用：fn(a1, a2)

/** 解析器状态：toks 是词法分析产出的 token 列表，i 是当前读到第几个（游标）。 */
interface Parser {
  toks: Token[];
  i: number;
}

/**
 * 二元运算符优先级表：数值越大绑定越紧。
 * 从低到高：|| → && → 相等比较 → 大小比较 → 加减 → 乘除。
 * Pratt 解析器靠它在循环里决定「先和谁结合」。
 */
const PRECEDENCE: Record<string, number> = {
  "||": 1,
  "&&": 2,
  "===": 3, "!==": 3, "==": 3, "!=": 3,
  ">": 4, ">=": 4, "<": 4, "<=": 4,
  "+": 5, "-": 5,
  "*": 6, "/": 6, "%": 6,
};

/** 关键字常量表：解析到这些 id 时直接变成字面量节点，不当普通变量名用。 */
const KEYWORDS: Record<string, any> = { true: true, false: false, null: null, undefined: undefined };

/** 解析入口：把源码字符串变成语法树（词法 + Pratt 解析一条链完成）。 */
function parse(source: string): Ast {
  const parser: Parser = { toks: tokenize(source), i: 0 };
  if (parser.toks.length === 0) throw new Error("表达式为空");
  const ast = parseExpr(parser, 1); // 从最低优先级 1 开始，解析完整表达式
  // 表达式解析完游标应正好到末尾；否则说明有多余内容，如 "1 2"
  if (parser.i < parser.toks.length) throw new Error("表达式有多余内容");
  return ast;
}

/** 看下一个 token 但不动游标（返回 undefined 表示已到末尾）。 */
const peek = (p: Parser): Token | undefined => p.toks[p.i];

/** 若下一个 token 正好是指定操作符就消费掉并返回 true，否则游标不动、返回 false。 */
function eat(p: Parser, op: string): boolean {
  const token = peek(p);
  if (token && token.t === "op" && token.v === op) {
    p.i += 1;
    return true;
  }
  return false;
}

/** 强制要求下一个 token 是指定操作符：消费它，否则抛「缺少 xx」错误。 */
function expect(p: Parser, op: string): void {
  if (!eat(p, op)) throw new Error(`缺少「${op}」`);
}

/**
 * Pratt 解析核心：解析「优先级不低于 minPrec」的表达式。
 *
 * 例：解析 1 + 2 * 3 时先取左值 1，遇到 '+'（优先级 5），
 * 递归解析右侧时要求优先级 > 5，于是 '*' 先和 2、3 结合，整体装成 1 + (2 * 3)。
 * 递归传 prec + 1 保证同级左结合：1 - 2 - 3 解析成 (1 - 2) - 3。
 */
function parseExpr(p: Parser, minPrec: number): Ast {
  let left = parseUnary(p); // 先解析一个前缀单元作为左操作数
  for (; ;) {
    const token = peek(p);
    if (!token || token.t !== "op") break;
    if (token.v === "?") {
      // 三元 c ? a : b：优先级最低；minPrec > 1 时属于上层，交给上层处理
      if (minPrec > 1) break;
      p.i += 1;
      const thenBranch = parseExpr(p, 1); // 两个分支都按完整表达式解析
      expect(p, ":");
      const elseBranch = parseExpr(p, 1);
      left = { k: "cond", c: left, a: thenBranch, b: elseBranch };
      continue;
    }
    // 普通二元运算：优先级不够高就退出循环，把结合权交回上一层
    const prec = PRECEDENCE[token.v];
    if (prec === undefined || prec < minPrec) break;
    p.i += 1;
    const right = parseExpr(p, prec + 1);
    left = { k: "bin", op: token.v, a: left, b: right };
  }
  return left;
}

/**
 * 解析一元前缀运算（! 和 -），绑定优先级高于所有二元运算。
 * 递归调用自身实现右结合，如 !!a、-(-b) 会层层嵌套。
 */
function parseUnary(p: Parser): Ast {
  const token = peek(p);
  if (token && token.t === "op" && (token.v === "!" || token.v === "-")) {
    p.i += 1;
    return { k: "un", op: token.v, a: parseUnary(p) }; // 前缀只作用于后面紧邻的部分
  }
  return parsePostfix(p);
}

/**
 * 解析后缀访问链：先取主表达式，再反复消费 `.prop` 和 `[expr]`，
 * 把 a.b[0].c 这类访问串成嵌套节点。
 */
function parsePostfix(p: Parser): Ast {
  let node = parsePrimary(p);
  for (; ;) {
    if (eat(p, ".")) {
      // 点号后必须是标识符，如 user.name
      const token = peek(p);
      if (!token || token.t !== "id") throw new Error("「.」后面需要属性名");
      p.i += 1;
      node = { k: "member", obj: node, prop: token.v };
    } else if (eat(p, "[")) {
      // 中括号里可以是任意表达式，如 list[i + 1]、map[key]
      const idx = parseExpr(p, 1);
      expect(p, "]");
      node = { k: "index", obj: node, idx };
    } else {
      return node; // 没有更多后缀，链条结束
    }
  }
}

/**
 * 解析最基础的「原子」：数字、字符串、关键字、变量名、函数调用、括号表达式。
 * 按 token 类型分派，不处理优先级（优先级交给 parseExpr）。
 */
function parsePrimary(p: Parser): Ast {
  const token = peek(p);
  if (!token) throw new Error("表达式不完整");
  p.i += 1; // 先消费当前 token，后面按类型分派
  if (token.t === "num") return { k: "lit", v: Number(token.v) }; // 这里才把数字文本转成 number
  if (token.t === "str") return { k: "lit", v: token.v };
  if (token.t === "id") {
    if (token.v in KEYWORDS) return { k: "lit", v: KEYWORDS[token.v] };
    if (eat(p, "(")) {
      // 形如 fn(...)：解析参数列表，逗号分隔
      const args: Ast[] = [];
      if (!eat(p, ")")) {
        // 空参数表是 ()，否则至少解析一个参数，直到遇到 ')' 结束
        for (; ;) {
          args.push(parseExpr(p, 1));
          if (eat(p, ")")) break;
          expect(p, ",");
        }
      }
      return { k: "call", name: token.v, args };
    }
    return { k: "id", name: token.v }; // 后面没有 '('，就是普通变量引用
  }
  if (token.v === "(") {
    // 括号只影响结合顺序，inner 直接作为结果，不给 AST 留节点
    const inner = parseExpr(p, 1);
    expect(p, ")");
    return inner;
  }
  throw new Error(`语法错误：「${token.v}」`);
}

// ---------------- 求值 ----------------

/**
 * 求值入口：递归遍历语法树。scope 是变量表（state / dataSources / env / record / event）。
 * 取属性 / 下标都用了可选链，数据还没加载好时得到 undefined 而不是抛错。
 */
function evalAst(ast: Ast, scope: ExprScope): any {
  switch (ast.k) {
    case "lit":
      return ast.v; // 字面量原样返回
    case "id":
      return scope[ast.name]; // 变量查表；不存在时是 undefined
    case "un": {
      const value = evalAst(ast.a, scope);
      return ast.op === "!" ? !value : -value; // ! 取反，- 取负
    }
    case "bin": {
      const op = ast.op;
      // && / || 短路求值：左边能定结果就不算右边（也避免右侧报错）
      if (op === "&&") {
        const left = evalAst(ast.a, scope);
        return left ? evalAst(ast.b, scope) : left;
      }
      if (op === "||") {
        const left = evalAst(ast.a, scope);
        return left || evalAst(ast.b, scope);
      }
      // 其他二元运算：先算两边，再按 JS 语义计算
      const a = evalAst(ast.a, scope);
      const b = evalAst(ast.b, scope);
      switch (op) {
        case "+": return a + b;
        case "-": return a - b;
        case "*": return a * b;
        case "/": return a / b;
        case "%": return a % b;
        case "===": return a === b;
        case "!==": return a !== b;
        case "==": return a == b;
        case "!=": return a != b;
        case ">": return a > b;
        case ">=": return a >= b;
        case "<": return a < b;
        case "<=": return a <= b;
        default: return undefined;
      }
    }
    case "cond":
      // 三元：条件为真取 a，否则取 b；两边只会计算命中那一支
      return evalAst(ast.c, scope) ? evalAst(ast.a, scope) : evalAst(ast.b, scope);
    case "member":
      return evalAst(ast.obj, scope)?.[ast.prop]; // obj 为 null / undefined 时返回 undefined
    case "index":
      return evalAst(ast.obj, scope)?.[evalAst(ast.idx, scope)]; // 下标表达式先求值再取值
    case "call": {
      // 只能调用白名单 HELPERS 里的函数，杜绝任意代码执行
      const fn = HELPERS[ast.name];
      if (!fn) throw new Error(`不支持的函数「${ast.name}」（可用：${Object.keys(HELPERS).join(" / ")}）`);
      return fn(...ast.args.map((arg) => evalAst(arg, scope))); // 逐个求值参数后调用
    }
  }
}

// ---------------- 对外 API ----------------

/** 求值一段纯表达式（不含 {{ }}）。出错时告警并返回 undefined，不让页面崩。 */
export function evalExpression(expression: string, scope: ExprScope): any {
  try {
    return evalAst(parse(expression), scope); // 先 tokenize + 建语法树，再递归求值
  } catch (error) {
    // 解析或求值出错都吞掉：只告警，返回 undefined 兜底
    console.warn("[lowcode] 表达式求值失败：", expression, error);
    return undefined;
  }
}

const RAW_EXPR = /^\s*\{\{([\s\S]+)\}\}\s*$/; // 整个字符串就是一个 {{...}}（允许前后空白）
const INLINE_EXPR = /\{\{([\s\S]+?)\}\}/g; // 字符串中夹杂的 {{...}}，非贪婪地逐个匹配

/** 求值一个值：整串是 {{ }} 时返回原始类型（数组/对象原样），否则按字符串插值。 */
export function evalValue(value: unknown, scope: ExprScope): unknown {
  if (typeof value !== "string") return value; // 非字符串（数字、数组等）无需求值
  const raw = value.match(RAW_EXPR);
  if (raw) return evalExpression(raw[1], scope); // 整串表达式：保留原始类型，如 {{ list }} 得到数组
  if (!value.includes("{{")) return value; // 不含模板标记，原样返回
  // 插值模式：把每个 {{...}} 的结果拼进字符串，null / undefined 显示为空串
  return value.replace(INLINE_EXPR, (_all, expression: string) => {
    const result = evalExpression(expression, scope);
    return result == null ? "" : String(result);
  });
}

/** 批量求值一层 props（数组/对象原样透传，不做深拷贝求值）。 */
export function evalRecord(
  record: Record<string, unknown> | undefined,
  scope: ExprScope,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  // 只遍历最外层：每个值交给 evalValue（是 {{ }} 就求值，否则原样返回）
  for (const [key, value] of Object.entries(record ?? {})) out[key] = evalValue(value, scope);
  return out;
}

/** 判断「可见性」这类表达式结果是否为真。 */
export function truthy(value: unknown): boolean {
  // 比 JS 真值更严格：false、null / undefined、空串、0、"false" 都算「假」
  return !(value === false || value == null || value === "" || value === 0 || value === "false");
}
