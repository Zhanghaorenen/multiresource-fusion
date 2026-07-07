## 多源异构数据时空对齐与融合系统


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


