<template>
  <div class="portal-splash">
    <div class="portal-splash-stage">
      <div class="portal-splash-canvas">
        <img
          class="portal-splash-image"
          src="/united-intelligence-splash.jpg"
          alt="联合智擎，让团队知识可连接，让智能体可行动"
        />
        <img class="portal-splash-logo" src="/united-intelligence-logo.png" alt="联合智擎" />

        <button class="portal-start-button" type="button" :disabled="isLoading" @click="goToChat">
          <LoaderCircle v-if="isLoading" :size="18" class="spinning" aria-hidden="true" />
          <span>{{ isLoading ? '正在连接' : '开始体验' }}</span>
          <ArrowRight v-if="!isLoading" :size="18" aria-hidden="true" />
        </button>

        <div v-if="error" class="portal-status-card" role="alert">
          <AlertCircle :size="18" aria-hidden="true" />
          <div>
            <strong>{{ error.title }}</strong>
            <span>{{ error.message }}</span>
          </div>
          <button type="button" @click="retryLoad">重新连接</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AlertCircle, ArrowRight, LoaderCircle } from '@lucide/vue'
import { healthApi } from '@/apis/system_api'
import { useInfoStore } from '@/stores/info'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const infoStore = useInfoStore()
const userStore = useUserStore()

const isLoading = ref(true)
const error = ref(null)

const loadPortal = async () => {
  isLoading.value = true
  error.value = null
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') throw new Error('服务暂不可用')
    await infoStore.loadInfoConfig()
  } catch (loadError) {
    console.error('加载门户失败:', loadError)
    error.value = {
      title: '服务连接失败',
      message: '暂时无法连接后端服务，请检查服务状态后重试。'
    }
  } finally {
    isLoading.value = false
  }
}

const retryLoad = () => {
  void loadPortal()
}

const goToChat = () => {
  if (!userStore.isLoggedIn) {
    sessionStorage.setItem('redirect', '/agent')
    router.push('/login')
    return
  }
  router.push('/agent')
}

onMounted(() => {
  void loadPortal()
})
</script>

<style lang="less" scoped>
.portal-splash {
  width: 100%;
  height: 100vh;
  min-width: var(--min-width);
  overflow: hidden;
  background: var(--portal-splash-backdrop);
}

.portal-splash-stage {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.portal-splash-canvas {
  position: relative;
  width: min(100vw, calc(100vh * 1672 / 941));
  aspect-ratio: 1672 / 941;
  flex: 0 0 auto;
  overflow: hidden;
}

.portal-splash-image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
  -webkit-user-drag: none;
}

.portal-splash-logo {
  position: absolute;
  z-index: 2;
  top: 3.6%;
  left: 2.8%;
  width: 13.2%;
  height: auto;
  object-fit: contain;
}

.portal-start-button {
  position: absolute;
  z-index: 3;
  top: 73.7%;
  left: 50%;
  width: 14.4%;
  min-width: 150px;
  min-height: 48px;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 20px;
  border: 0;
  border-radius: 999px;
  background: linear-gradient(
    90deg,
    var(--portal-brand-blue),
    var(--portal-brand-cyan),
    var(--portal-brand-green)
  );
  box-shadow: 0 12px 26px color-mix(in srgb, var(--portal-brand-blue) 26%, transparent);
  color: #ffffff;
  font-size: clamp(15px, 1.18vw, 19px);
  font-weight: 700;
  cursor: pointer;
  transition:
    box-shadow 0.18s ease,
    filter 0.18s ease;

  &:hover:not(:disabled),
  &:focus-visible {
    box-shadow: 0 16px 30px color-mix(in srgb, var(--portal-brand-blue) 32%, transparent);
    filter: saturate(1.05);
    outline: 3px solid color-mix(in srgb, var(--portal-brand-blue) 22%, transparent);
    outline-offset: 3px;
  }

  &:disabled {
    cursor: wait;
    opacity: 0.78;
  }
}

.portal-status-card {
  position: absolute;
  z-index: 4;
  right: 3%;
  bottom: 4%;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 380px;
  padding: 12px 14px;
  border: 1px solid var(--color-error-100);
  border-radius: 10px;
  background: color-mix(in srgb, var(--gray-0) 92%, transparent);
  color: var(--color-error-700);
  backdrop-filter: blur(10px);

  div {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  strong {
    font-size: 13px;
  }

  span {
    color: var(--gray-600);
    font-size: 12px;
  }

  button {
    flex: 0 0 auto;
    padding: 5px 8px;
    border: 1px solid var(--color-error-100);
    border-radius: 6px;
    background: var(--gray-0);
    color: var(--color-error-700);
    cursor: pointer;
  }
}

.spinning {
  animation: portal-spin 0.9s linear infinite;
}

@keyframes portal-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 900px) {
  .portal-splash-logo {
    left: 3%;
    width: 16%;
  }

  .portal-start-button {
    width: 17%;
    min-width: 136px;
    min-height: 44px;
  }
}

@media (max-width: 600px) {
  .portal-status-card {
    right: 16px;
    bottom: 16px;
    left: 16px;
    max-width: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
