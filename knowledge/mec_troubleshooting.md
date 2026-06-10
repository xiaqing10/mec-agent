# MEC 设备故障排查手册

## 常见故障类型

### 1. 物理机不可达
**症状**: SSH 无法连接物理机
**可能原因**:
- 设备关机或断电
- 网络路由不通
- SSH 服务未启动
- 防火墙阻断

**排查步骤**:
1. ping 设备 IP，检查网络连通性
2. 检查设备电源状态
3. 检查 SSH 服务状态: `systemctl status sshd`
4. 检查防火墙规则

### 2. Docker 服务异常
**症状**: Docker 服务未运行或异常
**可能原因**:
- Docker 守护进程崩溃
- 磁盘空间不足
- Docker 配置错误

**排查步骤**:
1. 检查 Docker 服务: `systemctl status docker`
2. 检查磁盘空间: `df -h`
3. 检查 Docker 日志: `journalctl -u docker`

### 3. 容器离线
**症状**: dev 容器不存在或未运行
**可能原因**:
- 容器被删除
- 容器崩溃
- 资源不足

**排查步骤**:
1. 检查容器状态: `docker ps -a`
2. 检查容器日志: `docker logs <container_id>`
3. 重启容器: `docker start <container_id>`

### 4. 进程异常
**症状**: supervisor 管理的进程状态异常
**可能原因**:
- 进程崩溃
- GPU 驱动异常
- 内存不足 (OOM)

**排查步骤**:
1. 检查进程状态: `supervisorctl status`
2. 检查进程日志: `supervisorctl tail <process_name>`
3. 检查系统日志: `dmesg | grep -i oom`

### 5. ROS 问题
**症状**: roscore 未运行或 topic 无数据
**可能原因**:
- roscore 进程崩溃
- ROS 配置错误
- 传感器连接异常

**排查步骤**:
1. 检查 roscore: `rosnode list`
2. 检查 topic: `rostopic list`
3. 检查 topic 频率: `rostopic hz <topic_name>`

### 6. 今日图片为 0
**症状**: 数据源无图片产生
**可能原因**:
- 相机连接异常
- 算法处理异常
- 存储空间不足

**排查步骤**:
1. 检查相机状态
2. 检查算法日志
3. 检查存储空间: `df -h /home/files`

## 修复操作指南

### 重启容器
```bash
docker restart <container_id>
```

### 重启进程
```bash
supervisorctl restart <process_name>
```

### 重启 Docker 服务
```bash
systemctl restart docker
```

### 清理磁盘空间
```bash
docker system prune -a
```

## 联系方式

如遇无法解决的问题，请联系运维团队。
