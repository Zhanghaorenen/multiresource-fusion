# MEx 多源时空对齐融合可视化

该 Web 界面直接读取 `mex.zip` 中的数据，按 7 类活动、受试者和试次组织样本，并统一展示为温度图片、振动数据、音频数据和视频数据四类多源异构模态。算法通过最近邻时间匹配建立统一时间轴，随后对四模态特征进行归一化加权融合。

## 启动

后端使用 Spring Boot，前端使用 Vue/Vite。首次运行需要安装前端依赖并构建静态资源：

```powershell
cd frontend
npm install
npm run build
cd ..
mvn spring-boot:run
```

浏览器访问 <http://127.0.0.1:8080>。

如果提示 `Port 8080 was already in use`，说明 8080 端口已有程序占用。可先查找并停止占用进程：

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
Stop-Process -Id <OwningProcess> -Force
```

也可以临时换端口启动：

```powershell
mvn spring-boot:run "-Dspring-boot.run.arguments=--server.port=8090"
```

此时浏览器访问 <http://127.0.0.1:8090>。

开发前端时可分别启动后端和 Vite：

```powershell
mvn spring-boot:run "-Dspring-boot.run.arguments=--server.port=8090"
cd frontend
npm run dev
```

Vite 开发地址为 <http://127.0.0.1:5173>，`/api` 默认代理到 <http://127.0.0.1:8090> 的 Spring Boot 后端。如需代理到其他端口，可设置 `VITE_API_PROXY_TARGET`。

界面包含原始时序预览、对齐进度、对齐前后波形、对齐质量指标、融合动画、最终判别结果、融合结果曲线、模态贡献饼图、质量指标表和 JSON 结果导出。由于 MEx 没有异常标签，页面的“正常/疑似异常/异常”是基于归一化融合响应强度的流程演示判别，不能作为真实异常识别准确率或医学诊断结论。
