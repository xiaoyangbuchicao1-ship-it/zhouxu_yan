# 工作脚本隔离

`native` 现在强制使用 [Bubblewrap](https://github.com/containers/bubblewrap)，不会在隔离失败时直接执行脚本。它复用现有工作区、文件工具与产物登记；不需要 Docker socket、特权容器或宿主机守护进程。

## 允许脚本看到什么

- `/workspace`：当前会话的工作文件，可写。
- `/workspace/.skills`：API 私有目录中该会话获准的技能包，只读。源码目录不在用户可写工作区内。
- 部署配置指定的内置 CSL 编译包、可信调用方显式指定的只读资源：只读。
- `/usr`、`/bin`、`/lib`、`/lib64`、字体配置与动态库缓存：运行 Python、文档工具所需的系统文件，只读。
- `/tmp`：每条命令独立的临时内存盘，按 `execution.tmpfs_size` 限制大小。
- `/proc`：新 PID 命名空间中的进程，不是 API 或宿主机的进程列表。

不会挂载整个 `/app`、`/root`、账户数据库、运行数据库或配置密钥。子进程使用固定的无密钥环境变量，继承的描述符仅有本次输入输出与启动隔离所需的临时过滤器。

每条命令拥有独立 user/mount/PID/network/IPC/UTS/cgroup 命名空间，移除 capabilities，并禁止提权。内层 libseccomp 过滤器禁止网络 socket（本地 AF_UNIX 除外）、再建命名空间、重新挂载、ptrace 和跨进程读写；子进程继承限制。

## 部署配置与来源

API 镜像安装 Debian 的 `bubblewrap` 与 `libseccomp2`；Compose 设置 `no-new-privileges` 和 `seccomp-execution.json`。原 Docker 默认策略阻止建立必要的命名空间，因此需要这个专用策略文件。

策略来源：[Moby 默认 seccomp 策略](https://github.com/moby/profiles/blob/a0fa70805f742f7da15b367770d802ea2a86c68b/seccomp/default.json)，版本 `a0fa70805f742f7da15b367770d802ea2a86c68b`，遵循 [Apache-2.0 许可证](https://github.com/moby/profiles/blob/a0fa70805f742f7da15b367770d802ea2a86c68b/LICENSE)。保留原始架构与系统调用规则，只追加允许 `unshare`、`mount`、`umount2`、`pivot_root`、`setns`、`clone` 的一条规则，用于可信 Bubblewrap 建立隔离环境；这些权限在运行用户代码前由内层过滤器撤销。未启用 `seccomp=unconfined`、`privileged` 或 Docker socket。

已实测环境：Docker Desktop Linux Engine / WSL2 Linux 6.6.87 / Debian Bookworm / Bubblewrap 0.8.0。其他 Linux 主机必须允许非特权 user namespaces；缺失内核能力或被宿主 LSM 阻止时，工作命令明确失败，不降级执行。API 普通问答不依赖该隔离能力。

## 资源与清理边界

- 命令及其子进程继承单进程地址空间、CPU 时间、进程数、文件大小和禁用 core dump 的硬限制。限制设置失败即中止启动。
- CPU 时间按命令超时秒数设置；墙上时间另由 API 监督，超时终止 Bubblewrap。PID 命名空间退出会清理后台进程，包括自行建立新会话的子进程。
- 输出用临时文件接收，避免无限输出占满 API 内存；单文件受文件大小限制，超过接口输出预算会明确失败。
- API 容器总资源默认限制为 `6g` 内存、`2` CPU、`512` 个进程；可通过 `ZHOUXU_API_MEMORY_LIMIT`、`ZHOUXU_API_CPUS`、`ZHOUXU_API_PIDS_LIMIT` 调整。它们同时覆盖 API、检索模型和并发工作命令。
- **没有承诺按 Run 聚合的 CPU/内存/PID 配额**。单进程限制不是进程树总限制；`RLIMIT_NPROC` 也不能替代容器级 `pids_limit`。同一 API 容器内的并发任务仍共享上述总预算。工作区持久文件也没有独立磁盘总配额。

## 五项真实进程验收

在构建完成的 API 镜像内运行 `backend.infrastructure.execution.test_native_isolation`，并给独立测试容器使用上述 seccomp 文件：

1. Python、文档库与子进程正常运行，当前工作区可以保存文件，实际资源上限与配置一致。
2. 另一工作区、外部进程环境、外部根目录及指向外部的软链接不可读取。
3. 获准的技能脚本能执行，但不能修改或移走只读技能包。
4. 网络 socket 与进一步创建命名空间被内核拒绝。
5. 命令超时后，脱离原进程组的后台子进程也停止写入。

这组测试验证进程边界。上传、授权、模型调用及产物下载还需通过实际 HTTP/浏览器入口另行验收。

论文编译入口也使用同一个隔离运行时，原始 CSL 编译脚本和模板不改写。`test_paper_compile_isolation` 另外真实验证双格式产物、编译失败清理、外部文件不可读及取消时的子进程清理。API 在启动编译前打开并固定输出目录，随后通过目录描述符读取、计算哈希与删除产物，防止编译期间输出路径被换成软链接后误操作其他账户文件。
