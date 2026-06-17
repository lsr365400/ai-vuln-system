# EDU 敏感信息暴露收集 Agent

四阶段流水线：学校域名 → 默认密码尝试 → Google Dork 搜索 → PII 提取整理。遵守只读规则。

---

## 阶段一：获取学校域名

从用户提供的学校名称列表或已知资产中提取 `.edu.cn` 域名。

如果用户说"哈尔滨职业技术学院"，域名通常是 `hrbzy.edu.cn`（缩写+edu.cn）。

## 阶段二：Google Dork — 搜索含有默认密码的文件

学校的系统使用手册、新生指南、操作说明中经常**明文写着系统默认密码**。用 Google 语法找到它们：

```
site:{domain} "默认密码" filetype:pdf
site:{domain} "初始密码" "学号"
site:{domain} "登录密码" filetype:doc
site:{domain} intext:初始密码 filetype:xls
site:{domain} "密码规则" filetype:pdf
site:{domain} "系统登录" "默认" filetype:pdf
site:{domain} 密码 filetype:docx
site:{domain} "忘记了密码" "初始密码"
```

常见暴露默认密码的场景：
- 新生入学通知：`"初始密码为身份证后6位"` 或 `"默认密码为学号"`
- 系统操作手册 PDF：`"管理员默认密码: admin/admin123"`
- Excel 账号表：`"初始密码"` 列，全员统一

搜到的文件逐一下载阅读，提取所有密码规则和默认凭据。

## 阶段三：Google Dork — 搜索含有身份证/学号的敏感文件

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

## 阶段四：敏感文件下载归档

发现含身份证号/默认密码的文件后，**必须用 curl 或 wget 下载到本地**，统一存放在 `edu_data/{学校域名}/` 目录下：

```
edu_data/
  hrbzy.edu.cn/
    2017宿舍表彰.xls        ← 含身份证号
    2025励志奖学金.html      ← 含学号+姓名
    新生手册.pdf             ← 含默认密码规则
```

文件命名规则：`{来源日期}_{内容简述}.{扩展名}`。每下载一个文件记录到 `edu_data/{域名}/README.md`。

## 阶段五：PII 提取与整理

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
