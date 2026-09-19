/**
 * 物料面板：按分类列出注册表里的组件。
 * 支持两种添加方式：拖到画布（推荐，可精确选择容器）、点击（加到当前选中容器的后面）。
 */

import { materialCategories } from "../materials/registry";

export function MaterialPanel({ onAdd }: { onAdd: (type: string) => void }) {
  return (
    <div className="lc-panel">
      <div className="lc-panel-title">物料（拖到画布，或点击添加）</div>
      {materialCategories().map((group) => (
        <div key={group.category} className="lc-material-group">
          <div className="lc-group-title">{group.category}</div>
          <div className="lc-materials">
            {group.items.map((item) => (
              <div
                key={item.type}
                className="lc-material"
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData("lc/material", item.type);
                  event.dataTransfer.effectAllowed = "copy";
                }}
                onClick={() => onAdd(item.type)}
              >
                {item.label}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
