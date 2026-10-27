// cs.nexbyte.top —— 智行客服部署流水线
// 底线：只操作 chatbot 自己的资源（/opt/chatbot、compose 项目 chatbot、cs.conf），不碰 shop/erp/workbench/www/ai-kb/jenkins
pipeline {
  agent any
  options {
    disableConcurrentBuilds()
    timeout(time: 120, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }
  parameters {
    string(name: 'BRANCH', defaultValue: 'main', description: '部署的 Git 分支')
  }
  environment {
    REPO_PATH  = 'https://github.com/yangjingquan/chatbot_system.git'
    DEPLOY_DIR = '/opt/chatbot'
    PROJECT    = 'chatbot'
    // 在宿主机上执行 compose 的一次性容器（复用宿主机 docker CLI 和 compose 插件）
    // 注意：不要在外层 docker run 加 --env-file /opt/chatbot/.env —— Jenkins 容器未挂载该路径，
    // CLI 会在客户端解析 --env-file 并失败。Sync 已 ln -sfn /opt/chatbot/.env → app/.env，
    // compose 在 -w /opt/chatbot/app 内会自动读取该 .env。
    RUNNER = 'docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /usr/bin/docker:/usr/bin/docker:ro -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro -e DOCKER_BUILDKIT=1 -e BUILDX_NO_DEFAULT_ATTESTATIONS=1 -e COMPOSE_PROJECT_NAME=chatbot -v /opt/chatbot:/opt/chatbot -w /opt/chatbot/app python:3.12-slim'
  }
  stages {
    stage('Validate') {
      steps {
        script {
          if (!(params.BRANCH ==~ /^[A-Za-z0-9._\/-]+$/) || params.BRANCH.contains('..')) { error "非法分支名：${params.BRANCH}" }
        }
      }
    }
    stage('Checkout') {
      steps {
        sh '''
          set -eux
          find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
          ok=0
          for p in https://ghfast.top/ https://ghproxy.net/ ""; do
            find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
            if timeout 180 git clone --depth 1 --single-branch --branch "$BRANCH" "${p}${REPO_PATH}" .; then ok=1; break; fi
          done
          [ "$ok" = 1 ]
          git rev-parse --short HEAD
          test -f compose.yaml
          test -f compose.prod.yaml
          test -f deploy/nginx/cs.conf
        '''
      }
    }
    stage('Preflight') {
      steps {
        sh '''
          set -eux
          docker inspect -f '{{.State.Running}}' shop-nginx | grep -qx true
          docker run --rm -v /opt/chatbot:/d:ro -v /etc/shop/tls:/t:ro alpine:3.20 sh -c 'test -s /d/.env && test -s /t/cs.nexbyte.top.pem && test -s /t/cs.nexbyte.top.key'
          free -m || true
          df -h /mnt/data /opt || true
        '''
      }
    }
    stage('Sync To /opt/chatbot/app') {
      steps {
        sh '''
          set -eux
          HOST_WS=$(printenv WORKSPACE | sed 's#^/var/jenkins_home#/opt/jenkins/jenkins_home#')
          docker run --rm -v "$HOST_WS":/src:ro -v /opt/chatbot:/target alpine:3.20 sh -euc '
            rm -rf /target/app.new && mkdir -p /target/app.new
            cp -a /src/. /target/app.new/ && rm -rf /target/app.new/.git
            # 不覆盖服务器生产 .env
            rm -f /target/app.new/.env
            rm -rf /target/app.old; if [ -d /target/app ]; then mv /target/app /target/app.old; fi
            mv /target/app.new /target/app && rm -rf /target/app.old
            mkdir -p /target/deploy/nginx /target/backups
            cp /target/app/deploy/nginx/*.conf /target/deploy/nginx/
            # compose 需要在 app 目录旁能读到 /opt/chatbot/.env —— 用符号链接
            ln -sfn /opt/chatbot/.env /target/app/.env'
        '''
      }
    }
    stage('Build') {
      steps {
        sh '''
          set -eux
          # backend 含 OCR/向量模型，首次构建较慢；按目录 tree 哈希跳过未变服务
          docker run --rm -v /opt/chatbot:/d alpine:3.20 sh -c 'touch /d/.build-state; cat /d/.build-state' > .build-state.old
          : > .build-state.new
          for svc in backend web; do
            # worker 与 backend 共用同一镜像
            path="$svc"
            [ "$svc" = backend ] && path=backend
            [ "$svc" = web ] && path=frontend
            h=$(git rev-parse "HEAD:$path")
            img="chatbot-$svc:latest"
            if grep -qx "$svc $h" .build-state.old && docker image inspect "$img" >/dev/null 2>&1; then
              echo "[skip] $svc 未变化（$h）"
            else
              start=$(date +%s)
              $RUNNER docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml build --progress=plain "$svc"
              echo "[built] $svc 用时 $(( $(date +%s) - start ))s"
            fi
            echo "$svc $h" >> .build-state.new
          done
          # worker 与 backend 同 Dockerfile；compose build worker 会再建一次，用 backend 哈希记录
          h=$(git rev-parse "HEAD:backend")
          if grep -qx "worker $h" .build-state.old && docker image inspect chatbot-worker:latest >/dev/null 2>&1; then
            echo "[skip] worker 未变化（$h）"
          else
            start=$(date +%s)
            $RUNNER docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml build --progress=plain worker
            echo "[built] worker 用时 $(( $(date +%s) - start ))s"
          fi
          echo "worker $h" >> .build-state.new
          docker run --rm -i -v /opt/chatbot:/d alpine:3.20 sh -c 'cat > /d/.build-state' < .build-state.new
        '''
      }
    }
    stage('Deploy') {
      steps {
        sh '''
          set -eux
          $RUNNER docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml up -d
          $RUNNER docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml ps
        '''
      }
    }
    stage('Ensure Nginx (cs only)') {
      steps {
        sh '''
          set -eux
          # 443：shop-nginx conf.d 打进镜像；宿主机 /opt/shop/nginx/conf.d 留一份，运行中容器用 docker cp
          SRC=/opt/shop/nginx/conf.d/cs.conf
          NEW=$(mktemp)
          docker run --rm -v /opt/chatbot:/d:ro alpine:3.20 cat /d/deploy/nginx/cs.conf > "$NEW"
          if ! cmp -s "$NEW" "$SRC"; then cp "$NEW" "$SRC"; fi
          if ! docker exec shop-nginx cat /etc/nginx/conf.d/cs.conf 2>/dev/null | cmp -s - "$SRC"; then
            docker cp "$SRC" shop-nginx:/etc/nginx/conf.d/cs.conf
            if docker exec shop-nginx nginx -t; then
              docker exec shop-nginx nginx -s reload
            else
              docker exec shop-nginx rm -f /etc/nginx/conf.d/cs.conf
              rm -f "$SRC"
              docker exec shop-nginx nginx -t
              echo "shop-nginx 配置检查失败，已移除 cs.conf"; exit 1
            fi
          fi
          rm -f "$NEW"
          # 80：jenkins-proxy 宿主机挂载 conf.d
          if [ -f /opt/chatbot/deploy/nginx/cs-http.conf ] && [ -d /opt/jenkins/nginx/conf.d ]; then
            CHANGED=$(docker run --rm -v /opt/chatbot:/d:ro -v /opt/jenkins/nginx/conf.d:/c alpine:3.20 sh -c 'if cmp -s /d/deploy/nginx/cs-http.conf /c/cs.conf; then echo no; else cp /d/deploy/nginx/cs-http.conf /c/cs.conf; echo yes; fi')
            if [ "$CHANGED" = yes ]; then
              if docker run --rm --network jenkins_default --add-host host.docker.internal:host-gateway \
                   -v /opt/jenkins/nginx/nginx.conf:/etc/nginx/nginx.conf:ro -v /opt/jenkins/nginx/conf.d:/etc/nginx/conf.d:ro \
                   nginx:1.27-alpine nginx -t; then
                docker kill -s HUP ai-shop-jenkins-proxy
              else
                docker run --rm -v /opt/jenkins/nginx/conf.d:/c alpine:3.20 rm -f /c/cs.conf
                echo "jenkins-proxy 配置检查失败，已移除 cs.conf"; exit 1
              fi
            fi
          fi
        '''
      }
    }
    stage('Verify') {
      steps {
        sh '''
          set -eux
          # 等 backend healthy（含模型加载 start_period）
          for i in $(seq 1 60); do
            s=$(docker inspect -f "{{.State.Health.Status}}" chatbot-backend-1 2>/dev/null || docker inspect -f "{{.State.Health.Status}}" $(docker ps -qf name=chatbot-backend) 2>/dev/null || true)
            [ "$s" = healthy ] && break
            sleep 5
          done
          [ "$s" = healthy ]
          # 本机 WEB_PORT
          curl -fsS -o /dev/null -m 10 http://127.0.0.1:18086/api/health || curl -fsS -o /dev/null -m 10 http://127.0.0.1:18086/
          # 经 shop-nginx
          ok=0
          for i in $(seq 1 18); do
            if docker exec shop-nginx wget -qO- -T 5 --no-check-certificate --header="Host: cs.nexbyte.top" https://127.0.0.1/api/health 2>/dev/null | grep -qiE "ok|healthy|status"; then
              ok=1; break
            fi
            if docker exec shop-nginx wget -qO /dev/null --no-check-certificate --header="Host: cs.nexbyte.top" https://127.0.0.1/ 2>/dev/null; then
              ok=1; break
            fi
            sleep 5
          done
          if [ "$ok" != 1 ]; then echo "Verify failed: cs.nexbyte.top via shop-nginx"; exit 1; fi
          docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" $(docker ps -qf name=chatbot) || true
        '''
      }
    }
  }
  post {
    failure {
      sh 'docker ps -a --filter name=chatbot --format "{{.Names}} {{.Status}}" || true; docker logs --tail=80 $(docker ps -aqf name=chatbot-backend) 2>&1 || true'
    }
  }
}
