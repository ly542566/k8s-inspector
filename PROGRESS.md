# K8s 巡检平台 - 进度记录

## 已完成
- [x] 节点巡检（node）：Ready 状态、污点、kubelet 版本

## 进行中
- [ ] Pod 巡检（pod）：Pending、CrashLoopBackOff、重启次数

## 待办
- [ ] PVC 巡检（pvc）：Pending、使用率
- [ ] 核心组件巡检（component）：Calico、CoreDNS、Ingress-Nginx
- [ ] 证书巡检（certificate）：API Server 证书有效期
- [ ] 钉钉告警推送
- [ ] 结果存 Elasticsearch
- [ ] Helm 化 + GitOps
