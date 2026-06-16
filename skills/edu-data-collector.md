# EDU 敏感信息暴露收集 Agent

三阶段流水线：学校域名 → Google Dork 搜索 → PII 提取整理。遵守只读规则，不访问需登录的页面。

---

## 阶段一：获取学校域名

从用户提供的学校名称列表或已知资产中提取 `.edu.cn` 域名。

如果用户说"哈尔滨职业技术学院"，域名通常是 `hrbzy.edu.cn`（缩写+edu.cn）。

## 阶段二：Google Dork 搜索

对每个域名执行以下 Google 搜索（用 WebSearch 或 WebFetch）：

```
site:{domain} 身份证 filetype:xls
site:{domain} 身份证 filetype:pdf
site:{domain} 学号 filetype:xls
site:{domain} 学号 filetype:csv
site:{domain} 学生信息 filetype:xls
site:{domain} 学生名单 filetype:pdf
site:{domain} 电话号码 filetype:xls
site:{domain} 身份证号 filetype:pdf
site:{domain} inurl:upload 身份证
site:{domain} inurl:excel 学号
```

每次搜索获取前 5 条结果，访问每个 URL 获取内容。

## 阶段三：PII 提取与整理

对获取到的文件/页面内容，用以下正则匹配：

```
身份证号: [1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]
手机号:   1[3-9]\d{9}
学号:     \d{8,12}
姓名前缀: (姓名|学生|xm|user|name)
```

## 输出格式

每个有效发现输出一行，格式：

```
[学校] [类型] [姓名] [身份证号] [手机号] [来源URL]
```

**只记同时包含姓名+身份证号的记录**（仅学号或仅姓名不算，edu 收录标准）。不要编造数据——只记录实际从 URL 获取到的内容。
