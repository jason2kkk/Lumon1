# Lumon 平台 — URL 跳转自动挖掘 对接文档

## 功能说明

通过构造一个带参数的 URL 链接，可以跳转到 Lumon 平台首页，自动将需求描述填入"一句话描述"挖掘模式的输入框，并自动开始挖掘。

---

## URL 格式

```
https://lumon.vbradar.com/?q={需求描述}&autostart=1
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `q` | 是 | 需求描述文本，**必须做 `encodeURIComponent` 编码**。建议长度 10-100 字（不超过 500 字符） |
| `autostart` | 否 | 传 `1` 表示自动开始挖掘；不传则只预填输入框，用户手动点击"开始挖掘" |

---

## 代码示例

### JavaScript — 新标签页打开（推荐）

```javascript
const query = "用户拍了很多照片但没有整理成有意义的记录"
const url = `https://lumon.vbradar.com/?q=${encodeURIComponent(query)}&autostart=1`
window.open(url, '_blank')
```

### JavaScript — 当前页跳转

```javascript
const query = "用户拍了很多照片但没有整理成有意义的记录"
window.location.href = `https://lumon.vbradar.com/?q=${encodeURIComponent(query)}&autostart=1`
```

### HTML 链接

```html
<a href="https://lumon.vbradar.com/?q=%E7%94%A8%E6%88%B7%E6%8B%8D%E4%BA%86%E5%BE%88%E5%A4%9A%E7%85%A7%E7%89%87%E4%BD%86%E6%B2%A1%E6%9C%89%E6%95%B4%E7%90%86%E6%88%90%E6%9C%89%E6%84%8F%E4%B9%89%E7%9A%84%E8%AE%B0%E5%BD%95&autostart=1"
   target="_blank">
  在 Lumon 中挖掘
</a>
```

---

## 注意事项

1. **`q` 参数必须做 `encodeURIComponent` 编码**，否则中文和特殊字符（空格、`&`、`=` 等）会导致参数解析错误
2. 每次打开链接会创建一次新的挖掘任务，挖掘过程大约需要 **3-5 分钟**
3. 如果用户当前有进行中的挖掘任务，新任务不会覆盖，页面会提示"已有挖掘任务进行中"
4. 需求描述建议写成**一句自然语言**，描述用户痛点或产品方向，例如：
   - "用户拍了很多照片但没有整理成有意义的记录"
   - "远程团队沟通效率低，信息分散在多个工具里"
   - "自由职业者缺乏简单好用的收支记账工具"
5. 不建议传入过长的文本（超过 500 字符会被截断）
