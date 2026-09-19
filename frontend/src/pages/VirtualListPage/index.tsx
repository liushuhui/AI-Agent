import { useEffect, useRef, useState } from "react";
import { Flex, Tag, Typography } from "antd";

import "./index.css";

/**
 * 虚拟滚动「最简版」：1 万行数据，DOM 里始终只有 14 行。
 *
 * 全部逻辑就三步：
 *   1：heights：每行的高。生成数据时先估一个，渲染出来后由 ResizeObserver 量到真实高度存回去；
 *   2：tops：把行高从头累加 = 每行的 y 坐标；scrollTop 落在哪个区间，哪行就是可视区第一行；
 *   3：从那一行开始只渲染 14 行，用绝对定位摆到各自的 y 坐标上。
 */

const COUNT = 10_000; // 数据总量
const VIEW = 560; // 可视区高度（px），传给滚动容器
const RENDER = 14; // 每次固定渲染 14 行：最矮的行也有 ~50px，足够盖住可视区，还自带缓冲

interface RowItem {
    id: number;
    title: string;
    desc: string;
    tags: string[];
    guess: number; // 粗略高度估值（渲染后会被真实高度替换）
}

const TITLES = ["同步工作区文件", "重建向量索引", "归档会话历史", "校验知识库快照", "合并日志切片", "编排审批流水"];
const DESCS = ["增量扫描完成，没有冲突。", "命中缓存，耗时下降 38%。", "有 3 个分片需要重建，已排队。", "严格模式校验全部通过。"];
const TAGS = ["增量", "全量", "GPU", "幂等", "夜间窗口"];

/** 生成 1 万行：描述有的有、有的没有，标签 0~4 个 —— 行高自然就参差不齐 */
function makeRows(count: number): RowItem[] {
    return Array.from({ length: count }, (_, i) => {
        const desc = i % 4 === 0 ? "" : DESCS[i % DESCS.length] + (i % 3 === 0 ? DESCS[(i * 2 + 1) % DESCS.length] : "");
        const tags = TAGS.slice(0, i % 5);
        return {
            id: i,
            title: `${TITLES[i % TITLES.length]} #${i + 1}`,
            desc,
            tags,
            // 实测形状：纯标题 50px、带一行标签 +26、带一条描述 +28（差一两像素肉眼看不出来）
            guess: 50 + (desc ? 28 : 0) + (tags.length ? 26 : 0),
        };
    });
}

export function VirtualListPage() {
    const [rows] = useState(() => makeRows(COUNT));
    const [heights, setHeights] = useState<number[]>(() => rows.map((row) => row.guess));
    const [scrollTop, setScrollTop] = useState(0);
    const boxRef = useRef<HTMLDivElement>(null);

    // 每行的 y 坐标 = 前面所有行高之和（1 万次加法，一瞬间的事）
    const tops: number[] = [0];
    for (const h of heights) tops.push(tops[tops.length - 1] + h);

    // 最后一个「顶部 ≤ scrollTop」的行，就是可视区第一行
    const start = tops.findLastIndex((y) => y <= scrollTop);
    const visible = rows.slice(start, start + RENDER);

    useEffect(() => {
        const box = boxRef.current;
        if (!box) return;
        const observer = new ResizeObserver((entries) => {
            setHeights((prev) => {
                const next = [...prev];
                let changed = false;
                for (const entry of entries) {
                    const el = entry.target as HTMLElement;
                    const i = Number(el.dataset.i);
                    const h = Math.round(entry.borderBoxSize?.[0]?.blockSize ?? el.offsetHeight);
                    if (Number.isInteger(i) && h > 0 && next[i] !== h) {
                        next[i] = h;
                        changed = true;
                    }
                }
                return changed ? next : prev; // 高度没变就原样返回，不触发渲染
            });
        });
        box.querySelectorAll("[data-i]").forEach((el) => observer.observe(el));
        return () => observer.disconnect();
    });

    return (
        <div className="vpage">
            <Flex vertical gap={6} className="vpage-head">
                <Flex align="center" gap={10} wrap>
                    <Typography.Title level={3} style={{ margin: 0 }}>
                        虚拟滚动
                    </Typography.Title>
                    <Tag color="geekblue">{COUNT.toLocaleString("zh-CN")} 行数据</Tag>
                    <Tag>行高不定</Tag>
                </Flex>
                <Typography.Text type="secondary">
                    滚动条长度由「所有行高之和」撑出来，但页面里始终只渲染 14 行；其它行只是一串高度数字，根本不进 DOM。
                </Typography.Text>
            </Flex>

            <div ref={boxRef} className="vbox" style={{ height: VIEW }} onScroll={(e) => {
                setScrollTop(e.currentTarget.scrollTop)
            }}>
                {/* 占位块：高度 = 所有行高之和，让滚动条和 1 万行内容一样长 */}
                <div className="vbox-total" style={{ height: tops[COUNT] }}>
                    {visible.map((row, k) => {
                        const i = start + k;
                        return (
                            <div key={row.id} data-i={i} className="vbox-item" style={{ top: tops[i] }}>
                                <Row row={row} />
                            </div>
                        );
                    })}
                </div>
            </div>

            <Typography.Text type="secondary" className="vpage-foot">
                当前渲染第 {start + 1} ~ {start + visible.length} 行 · 滚动位置{" "}
                {Math.round(scrollTop).toLocaleString("zh-CN")} px
            </Typography.Text>
        </div>
    );
}

/** 一行长啥样：标题 +（可能有）描述 +（可能有）标签 */
function Row({ row }: { row: RowItem }) {
    return (
        <div className="vrow">
            <Typography.Text strong>{row.title}</Typography.Text>
            {row.desc && (
                <div className="vrow-desc">
                    <Typography.Text type="secondary">{row.desc}</Typography.Text>
                </div>
            )}
            {row.tags.length > 0 && (
                <Flex gap={4} wrap className="vrow-tags">
                    {row.tags.map((tag) => (
                        <Tag key={tag} color="geekblue">
                            {tag}
                        </Tag>
                    ))}
                </Flex>
            )}
        </div>
    );
}
