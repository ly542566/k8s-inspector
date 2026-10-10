# K8s 集群自动化巡检平台

基于 Python + Kubernetes API 开发的可扩展集群巡检系统，通过 CronJob 定时执行，发现异常自动邮件告警。

## 功能特性

- 6 个巡检维度：Node、Pod、PVC、核心组件、Deployment、镜像拉取
- 可扩展框架：装饰器注册 + ConfigMap 配置驱动
- 最小权限：ServiceAccount + ClusterRole + ClusterRoleBinding
- 邮件告警：集成 SMTP，异常时自动推送报告
- 定时调度：基于 K8s CronJob

## 巡检项

| 检查项 | 检查内容 |
|:---|:---|
| node | Ready 状态、污点、kubelet 版本 |
| pod | Pending、Failed、CrashLoopBackOff、重启次数 |
| pvc | Pending、Lost |
| component | Calico、CoreDNS、kube-proxy、Ingress-Nginx |
| deployment | 期望副本数 vs 实际可用 |
| image | ImagePullBackOff、ErrImagePull |

## 快速开始

kubectl apply -f rbac.yaml
kubectl apply -f configmap.yaml
kubectl apply -f cronjob.yaml

## 排障记录

- CoreDNS 启动竞争导致 Harbor 组件反复重启
- RBAC 缺失 apps API group 导致 403 Forbidden
- QQ 邮箱 SMTP 的 From 头格式问题

## 技术栈

Python 3.11、Kubernetes API、RBAC、CronJob、ConfigMap、SMTP
