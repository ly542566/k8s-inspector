# ============================================================
# 导入需要的库
# ============================================================
import os                          # 操作系统接口，用来读环境变量
import yaml                        # 解析 YAML 配置文件
import logging                     # 日志模块，输出到 kubectl logs
from kubernetes import client, config   # K8s 官方 Python 客户端
import smtplib
import os
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime

# ============================================================
# 配置日志格式
# level=INFO：只输出 INFO 及以上级别（INFO/WARNING/ERROR）
# format：日志前缀格式，比如 [INFO] xxx
# ============================================================
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)   # 获取当前模块的 logger 对象


# ============================================================
# 检查项注册机制
# ============================================================
# CHECKS 是一个字典，用来存放 "检查项名字 -> 对应的函数"
# 比如：CHECKS = {"node": check_nodes, "pod": check_pods}
CHECKS = {}


def register_check(name):
    """
    装饰器：把一个函数注册到 CHECKS 字典里。
    
    用法：
        @register_check("node")
        def check_nodes(cfg):
            ...
    
    等价于：
        def check_nodes(cfg): ...
        CHECKS["node"] = check_nodes
    
    为什么用装饰器？
        - 加新检查项时，只需要在函数上面写 @register_check("xxx")
        - 不用手动去修改 CHECKS 字典
        - 主流程会自动从 CHECKS 里找到对应函数
    """
    def wrapper(func):
        # 把被装饰的函数存进 CHECKS 字典，key 是 name
        CHECKS[name] = func
        # 返回原函数（不改它的行为，只是顺便注册了一下）
        return func
    return wrapper


# ============================================================
# 函数：加载 K8s 配置
# ============================================================
def load_k8s_config():
    """
    加载 K8s 的连接配置。
    
    有两种情况：
        1. 在 K8s 集群内部的 Pod 里跑：
           - 自动读取 ServiceAccount 的 Token
           - 调用 load_incluster_config()
        2. 在本地电脑上跑（调试用）：
           - 读取 ~/.kube/config
           - 调用 load_kube_config()
    
    为什么用 try/except？
        - 集群里跑时，load_incluster_config() 会成功
        - 本地跑时，它会抛异常，这时再 fallback 到 load_kube_config()
    """
    try:
        config.load_incluster_config()      # 尝试读集群内配置
        logger.info("已加载集群内配置")
    except Exception:
        config.load_kube_config()           # 失败则读本地 kubeconfig
        logger.info("已加载本地 kubeconfig")


# ============================================================
# 函数：加载巡检配置文件
# ============================================================
def load_inspector_config(path="/etc/inspector/config.yaml"):
    """
    从 ConfigMap 挂载的路径里读取 YAML 配置。
    
    参数：
        path：配置文件路径，默认是 /etc/inspector/config.yaml
              （这个路径由 CronJob 里的 volumeMounts 决定）
    
    返回：
        一个字典，比如：
        {
            "checks": {
                "node": {"enabled": True, "check_ready": True}
            }
        }
    
    如果文件不存在（比如本地测试时没挂载 ConfigMap），
    返回一个默认配置，保证脚本不会崩。
    """
    try:
        # open(path, "r")：以只读模式打开文件
        # with ... as f：用完后自动关闭文件
        with open(path, "r") as f:
            return yaml.safe_load(f)        # 把 YAML 解析成 Python 字典
    except FileNotFoundError:
        logger.warning(f"配置文件 {path} 不存在，使用默认配置")
        return {
            "checks": {
                "node": {"enabled": True},
                "pod": {"enabled": True, "restart_threshold": 5,
                        "ignore_namespaces": ["kube-system"]},
                "pvc": {"enabled": True, "ignore_namespaces": ["kube-system"]},
                "component": {"enabled": True},
                "deployment": {"enabled": True, "ignore_namespaces": ["kube-system"]},
                "image": {"enabled": True},
            }
        }


# ============================================================
# 检查项：节点状态
# @register_check("node") 表示把这个函数注册到 CHECKS["node"]
# ============================================================
@register_check("node")
def check_nodes(cfg):
    """
    巡检所有节点的状态。
    
    参数：
        cfg：ConfigMap 里 checks.node 这一段的配置，比如：
             {"enabled": True, "check_ready": True, "check_taints": True}
    
    返回：
        issues：一个列表，存放所有发现的异常描述
                如果没有异常，返回空列表 []
    """
    # 创建 CoreV1Api 客户端，用来调用 K8s 核心 API
    # 比如：查节点、查 Pod、查 Service
    v1 = client.CoreV1Api()

    # 调用 API 拿所有节点
    # list_node() 返回一个对象，.items 是节点列表
    nodes = v1.list_node().items

    # 初始化异常列表
    issues = []

    logger.info(f"发现 {len(nodes)} 个节点")

    # 遍历每个节点
    for node in nodes:
        # node.metadata.name 是节点名字，比如 "k8s-master"
        name = node.metadata.name

        # ---------- 检查 1：节点是否 Ready ----------
        # cfg.get("check_ready", True) 表示：
        #   如果配置里有 check_ready，就用它的值
        #   如果没有，默认用 True
        if cfg.get("check_ready", True):
            ready = False       # 先假设不 Ready

            # node.status.conditions 是一个列表，包含各种状态条件
            # 我们只关心 type == "Ready" 的那一条
            for cond in node.status.conditions:
                if cond.type == "Ready":
                    # cond.status 是字符串 "True" 或 "False"
                    ready = (cond.status == "True")
                    break       # 找到 Ready 就跳出循环

            # 根据 ready 的结果输出日志
            if ready:
                logger.info(f"[OK] {name}: Ready")
            else:
                logger.warning(f"[异常] {name}: NotReady")
                issues.append(f"节点 {name} 不是 Ready 状态")

        # ---------- 检查 2：节点是否有污点 ----------
        if cfg.get("check_taints", True):
            # node.spec.taints 可能是 None，也可能是列表
            # 用 `or []` 保证它至少是空列表，避免 for 报错
            taints = node.spec.taints or []

            if taints:
                logger.info(f"[信息] {name}: 有 {len(taints)} 个污点")
                for t in taints:
                    # t.key / t.value / t.effect 是污点的三个属性
                    logger.info(f"       - {t.key}={t.value}:{t.effect}")

        # ---------- 附加信息：kubelet 版本 ----------
        # node.status.node_info.kubelet_version 是节点的 kubelet 版本
        kubelet_version = node.status.node_info.kubelet_version
        logger.info(f"[信息] {name}: kubelet {kubelet_version}")

    # 返回异常列表给主流程
    return issues

# ============================================================
# 函数：发送邮件告警
# ============================================================
def send_email(smtp_host, smtp_port, user, password, to_addr, subject, content):
    try:
        # 构造邮件
        msg = MIMEText(content, "plain", "utf-8")
        
        # 关键修正：直接使用字符串格式，不要用 Header 包装
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = Header(subject, "utf-8") # Subject 用 Header 编码是安全的

        # 连接 SMTP 服务器（SSL 加密）
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        server.login(user, password)          # 登录
        server.sendmail(user, [to_addr], msg.as_string())  # 发送
        server.quit()                          # 断开

        logger.info(f"邮件已发送到 {to_addr}")
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")

# ============================================================
# 主流程
# ============================================================
def run():
    """
    主函数：整个巡检流程的入口。
    
    执行顺序：
        1. 打印开始日志
        2. 加载 K8s 连接
        3. 读 ConfigMap 里的巡检配置
        4. 遍历配置里启用的每个检查项
        5. 调用对应的函数执行检查
        6. 汇总所有异常，输出
    """
    logger.info("=" * 50)
    logger.info("K8s 集群巡检开始")
    logger.info("=" * 50)

    # 步骤 1：加载 K8s 连接
    load_k8s_config()

    # 步骤 2：读取配置
    cfg = load_inspector_config()

    # 初始化总异常列表
    all_issues = []

    # 步骤 3：遍历配置里的每个检查项
    # cfg.get("checks", {}) 返回 checks 字典，如果没配就返回空字典
    # .items() 返回 (key, value) 元组，比如 ("node", {...})
    for check_name, check_cfg in cfg.get("checks", {}).items():

        # 如果这个检查项 enabled=False，跳过
        if not check_cfg.get("enabled", True):
            logger.info(f"检查项 [{check_name}] 已禁用，跳过")
            continue

        # 从 CHECKS 字典里找对应的函数
        # 比如 check_name="node"，就找 CHECKS["node"]
        func = CHECKS.get(check_name)

        # 如果找不到对应的函数（配置里写了但代码没实现）
        if not func:
            logger.warning(f"未找到检查项 [{check_name}] 的实现，跳过")
            continue

        # 找到就执行
        logger.info(f"--- 执行检查项: {check_name} ---")
        issues = func(check_cfg)        # 调用检查函数
        all_issues.extend(issues)       # 把返回的异常追加到总列表

    # 步骤 4：汇总输出
    logger.info("=" * 50)
    logger.info(f"巡检完成，共发现 {len(all_issues)} 个异常")
    for issue in all_issues:
        logger.warning(f"  - {issue}")
    logger.info("=" * 50)

    # 步骤 5：发送邮件告警
    smtp_host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    mail_to = os.environ.get("MAIL_TO", "")

    # 构造邮件正文
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if all_issues:
        subject = f"🚨 K8s 巡检发现 {len(all_issues)} 个异常"
        content = f"巡检时间：{now}\n\n"
        content += f"异常数量：{len(all_issues)}\n\n"
        content += "异常列表：\n"
        for issue in all_issues:
            content += f"  - {issue}\n"
    else:
        subject = "✅ K8s 巡检正常"
        content = f"巡检时间：{now}\n\n所有检查项正常。"

    # 如果有配置邮箱，就发送
    if smtp_user and mail_to:
        send_email(smtp_host, smtp_port, smtp_user, smtp_pass, mail_to, subject, content)
    else:
        logger.warning("未配置 SMTP，跳过邮件推送")

# ============================================================
# 检查项：Pod 状态
# ============================================================
@register_check("pod")
def check_pods(cfg):
    """
    巡检所有命名空间的 Pod。

    检查内容：
        1. Pending 状态的 Pod
        2. Failed 状态的 Pod
        3. CrashLoopBackOff 的容器
        4. 重启次数超过阈值的容器

    参数：
        cfg：ConfigMap 里 checks.pod 的配置
             {
                 "enabled": True,
                 "restart_threshold": 5,
                 "ignore_namespaces": ["kube-system"]
             }
    """
    v1 = client.CoreV1Api()

    # 拿所有 Pod（跨命名空间）
    pods = v1.list_pod_for_all_namespaces().items

    # 初始化异常列表
    issues = []

    # 从配置里读阈值，默认 5
    restart_threshold = cfg.get("restart_threshold", 5)

    # 忽略的命名空间，默认忽略 kube-system
    ignore_ns = cfg.get("ignore_namespaces", ["kube-system"])

    logger.info(f"发现 {len(pods)} 个 Pod（忽略 {ignore_ns}）")

    for pod in pods:
        ns = pod.metadata.namespace
        name = pod.metadata.name

        # 跳过忽略的命名空间
        if ns in ignore_ns:
            continue

        # ---------- 检查 1：Pending ----------
        if pod.status.phase == "Pending":
            issues.append(f"Pod {ns}/{name} 处于 Pending")
            logger.warning(f"[异常] {ns}/{name}: Pending")

        # ---------- 检查 2：Failed ----------
        elif pod.status.phase == "Failed":
            issues.append(f"Pod {ns}/{name} 处于 Failed")
            logger.warning(f"[异常] {ns}/{name}: Failed")

        # ---------- 检查 3：容器状态 ----------
        # pod.status.container_statuses 可能是 None
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                # 检查是否 CrashLoopBackOff
                # cs.state.waiting.reason 可能是 "CrashLoopBackOff"
                waiting = cs.state.waiting
                if waiting and waiting.reason == "CrashLoopBackOff":
                    issues.append(f"Pod {ns}/{name} 容器 {cs.name} CrashLoopBackOff")
                    logger.warning(f"[异常] {ns}/{name}/{cs.name}: CrashLoopBackOff")

                # 检查重启次数
                if cs.restart_count > restart_threshold:
                    issues.append(
                        f"Pod {ns}/{name} 容器 {cs.name} 重启 {cs.restart_count} 次"
                    )
                    logger.warning(
                        f"[异常] {ns}/{name}/{cs.name}: 重启 {cs.restart_count} 次"
                    )

    logger.info(f"Pod 巡检完成，发现 {len(issues)} 个异常")
    return issues

# ============================================================
# 检查项：核心组件状态
# ============================================================
@register_check("component")
def check_components(cfg):
    """
    巡检集群核心组件是否 Running。

    检查：
        - kube-system 下的 calico-node、coredns、kube-proxy
        - ingress-nginx 下的 ingress-nginx-controller

    参数：
        cfg：{"enabled": True, "required_pods": {...}}
    """
    v1 = client.CoreV1Api()
    issues = []

    # 默认要检查的关键组件（按 命名空间: 组件名标签 组织）
    required = cfg.get("required_pods", {
        "kube-system": ["calico-node", "coredns", "kube-proxy"],
        "ingress-nginx": ["ingress-nginx-controller"],
    })

    # 遍历每个命名空间
    for ns, names in required.items():
        # 拿这个命名空间下所有 Pod
        pods = v1.list_namespaced_pod(namespace=ns).items

        for keyword in names:
            # 找出名字里包含 keyword 的 Pod
            matched = [p for p in pods if keyword in p.metadata.name]

            if not matched:
                issues.append(f"核心组件 {ns}/{keyword} 未找到")
                logger.warning(f"[异常] {ns}/{keyword}: 未找到")
                continue

            # 逐个检查状态
            for pod in matched:
                name = pod.metadata.name
                phase = pod.status.phase

                if phase != "Running":
                    issues.append(f"核心组件 {ns}/{name} 状态为 {phase}")
                    logger.warning(f"[异常] {ns}/{name}: {phase}")
                else:
                    logger.info(f"[OK] {ns}/{name}: Running")

    logger.info(f"核心组件巡检完成，发现 {len(issues)} 个异常")
    return issues


# ============================================================
# 检查项：Deployment 副本数
# ============================================================
@register_check("deployment")
def check_deployments(cfg):
    """
    巡检 Deployment 的副本数是否匹配。

    检查：
        - spec.replicas（期望副本数）
        - status.available_replicas（实际可用副本数）
        两者不一致就报警。

    参数：
        cfg：{"enabled": True, "ignore_namespaces": ["kube-system"]}
    """
    apps_api = client.AppsV1Api()
    issues = []
    ignore_ns = cfg.get("ignore_namespaces", ["kube-system"])

    # 拿所有命名空间的 Deployment
    deployments = apps_api.list_deployment_for_all_namespaces().items

    logger.info(f"发现 {len(deployments)} 个 Deployment")

    for deploy in deployments:
        ns = deploy.metadata.namespace
        name = deploy.metadata.name

        if ns in ignore_ns:
            continue

        # 期望副本数（默认 1）
        desired = deploy.spec.replicas or 0

        # 实际可用副本数（可能为 None）
        available = deploy.status.available_replicas or 0

        if available < desired:
            issues.append(
                f"Deployment {ns}/{name} 期望 {desired} 副本，实际可用 {available}"
            )
            logger.warning(
                f"[异常] {ns}/{name}: 期望 {desired}, 可用 {available}"
            )
        else:
            logger.info(f"[OK] {ns}/{name}: {available}/{desired}")

    logger.info(f"Deployment 巡检完成，发现 {len(issues)} 个异常")
    return issues


# ============================================================
# 检查项：镜像拉取失败
# ============================================================
@register_check("image")
def check_images(cfg):
    """
    巡检是否有 Pod 卡在 ImagePullBackOff / ErrImagePull。

    参数：
        cfg：{"enabled": True}
    """
    v1 = client.CoreV1Api()
    issues = []

    pods = v1.list_pod_for_all_namespaces().items
    logger.info(f"扫描 {len(pods)} 个 Pod 的镜像状态")

    for pod in pods:
        ns = pod.metadata.namespace
        name = pod.metadata.name

        # container_statuses 可能为 None
        if not pod.status.container_statuses:
            continue

        for cs in pod.status.container_statuses:
            waiting = cs.state.waiting
            if waiting and waiting.reason in ("ImagePullBackOff", "ErrImagePull"):
                issues.append(
                    f"Pod {ns}/{name} 容器 {cs.name} 镜像拉取失败: {waiting.reason}"
                )
                logger.warning(
                    f"[异常] {ns}/{name}/{cs.name}: {waiting.reason}"
                )

    logger.info(f"镜像巡检完成，发现 {len(issues)} 个异常")
    return issues

@register_check("pvc")
def check_pvc(cfg):
    v1 = client.CoreV1Api()
    pvcs = v1.list_persistent_volume_claim_for_all_namespaces().items
    issues = []
    ignore_ns = cfg.get("ignore_namespaces", ["kube-system"])

    logger.info(f"发现 {len(pvcs)} 个 PVC")

    for pvc in pvcs:
        ns = pvc.metadata.namespace
        name = pvc.metadata.name
        if ns in ignore_ns:
            continue
        if pvc.status.phase == "Pending":
            issues.append(f"PVC {ns}/{name} 处于 Pending")
            logger.warning(f"[异常] {ns}/{name}: Pending")
        elif pvc.status.phase == "Lost":
            issues.append(f"PVC {ns}/{name} 处于 Lost")
            logger.warning(f"[异常] {ns}/{name}: Lost")

    logger.info(f"PVC 巡检完成，发现 {len(issues)} 个异常")
    return issues
# ============================================================
# 程序入口
# Python 的固定写法：
#   只有直接运行 python inspector.py 时，才执行 run()
#   如果被其他文件 import，则不执行
# ============================================================
if __name__ == "__main__":
    run()
