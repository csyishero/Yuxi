<template>
  <div class="account-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">账户设置</div>
        <p class="section-description">管理当前账户资料、身份信息。</p>
      </div>
      <a-button class="lucide-icon-btn" :loading="refreshing" @click="refreshProfile">
        <template #icon><RefreshCw :size="16" :class="{ spin: refreshing }" /></template>
        刷新
      </a-button>
    </div>

    <div class="account-card profile-card">
      <div class="profile-card-heading">
        <div>
          <div class="profile-card-title">账户资料</div>
          <p class="profile-card-description">查看和维护当前账户的基础信息。</p>
        </div>
        <a-button class="lucide-icon-btn password-entry-button" @click="openPasswordModal">
          <template #icon><KeyRound :size="16" /></template>
          修改密码
        </a-button>
      </div>

      <div class="profile-summary">
        <div class="profile-left">
          <a-upload
            :show-upload-list="false"
            :before-upload="beforeUpload"
            @change="handleAvatarChange"
            accept="image/*"
          >
            <div class="avatar-upload" :class="{ uploading: avatarUploading }">
              <FallbackAvatar
                :src="userStore.avatar"
                :default-src="avatarDefaultSrc"
                :name="userStore.username"
                :seed="userStore.uid || userStore.username"
                kind="user"
                :size="80"
                shape="circle"
                :alt="userStore.username"
                class="account-avatar"
              />
              <div class="avatar-mask">
                <Upload v-if="!avatarUploading" :size="16" />
                <RefreshCw v-else :size="16" class="spin" />
                <span>{{ userStore.avatar ? '更换' : '上传' }}</span>
              </div>
            </div>
          </a-upload>

          <div class="profile-fields">
            <div class="profile-row editable-row">
              <span class="profile-label">用户名</span>
              <a-input
                v-if="editingField === 'username'"
                ref="usernameInput"
                v-model:value="profileDraft.username"
                class="inline-input"
                size="small"
                :max-length="20"
                :disabled="savingField === 'username'"
                @press-enter="saveField('username')"
                @keydown.esc.stop.prevent="cancelField"
                @blur="cancelField"
              />
              <button
                v-else
                type="button"
                class="editable-value"
                @click="startFieldEdit('username')"
              >
                {{ userStore.username || '未设置' }}
              </button>
            </div>
            <div class="profile-row editable-row">
              <span class="profile-label">手机号</span>
              <a-input
                v-if="editingField === 'phone_number'"
                ref="phoneInput"
                v-model:value="profileDraft.phone_number"
                class="inline-input"
                size="small"
                :max-length="11"
                :disabled="savingField === 'phone_number'"
                @press-enter="saveField('phone_number')"
                @keydown.esc.stop.prevent="cancelField"
                @blur="cancelField"
              />
              <button
                v-else
                type="button"
                class="editable-value"
                @click="startFieldEdit('phone_number')"
              >
                {{ userStore.phoneNumber || '未设置' }}
              </button>
            </div>
            <div class="profile-row">
              <span class="profile-label">UID</span>
              <span class="profile-value mono">{{ userStore.uid || '未设置' }}</span>
            </div>
          </div>
        </div>

        <div class="identity-panel">
          <div class="identity-item">
            <span class="identity-icon"><ShieldCheck :size="15" /></span>
            <span class="profile-label">权限</span>
            <span class="profile-value" :style="{ color: getRoleColor(userStore.userRole) }">
              {{ userRoleText }}
            </span>
          </div>
          <div class="identity-item">
            <span class="identity-icon"><Building2 :size="15" /></span>
            <span class="profile-label">部门</span>
            <span class="profile-value">{{ userStore.departmentName || '默认部门' }}</span>
          </div>
        </div>
      </div>
      <UserConfigSettingsCard ref="userConfigRef" />
    </div>

    <a-modal
      v-model:open="passwordModalVisible"
      title="修改登录密码"
      width="520px"
      ok-text="确认修改"
      cancel-text="取消"
      :confirm-loading="changingPassword"
      :mask-closable="!changingPassword"
      :closable="!changingPassword"
      :cancel-button-props="{ disabled: changingPassword }"
      @ok="handlePasswordChange"
      @cancel="closePasswordModal"
      @after-close="resetPasswordDraft"
    >
      <div class="password-modal-content">
        <p class="password-modal-description">
          验证当前密码后设置新密码，修改成功后当前登录会失效，需要重新登录。
        </p>

        <a-form class="password-form" layout="vertical" :model="passwordDraft">
          <a-form-item label="当前密码" name="currentPassword" required>
            <a-input-password
              v-model:value="passwordDraft.currentPassword"
              name="current-password"
              autocomplete="current-password"
              placeholder="请输入当前密码"
              :disabled="changingPassword"
            />
          </a-form-item>

          <a-form-item label="新密码" name="newPassword" required>
            <a-input-password
              v-model:value="passwordDraft.newPassword"
              name="new-password"
              autocomplete="new-password"
              :placeholder="`请输入新密码（至少 ${MIN_PASSWORD_LENGTH} 位）`"
              :minlength="MIN_PASSWORD_LENGTH"
              :disabled="changingPassword"
            />
          </a-form-item>

          <a-form-item label="确认新密码" name="confirmPassword" required>
            <a-input-password
              v-model:value="passwordDraft.confirmPassword"
              name="confirm-password"
              autocomplete="new-password"
              placeholder="请再次输入新密码"
              :disabled="changingPassword"
              @press-enter="handlePasswordChange"
            />
          </a-form-item>
        </a-form>

        <div class="password-requirement">
          密码至少需要 {{ MIN_PASSWORD_LENGTH }} 个字符，且不能与当前密码相同。
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import UserConfigSettingsCard from '@/components/UserConfigSettingsCard.vue'

import { computed, nextTick, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Building2, KeyRound, RefreshCw, ShieldCheck, Upload } from '@lucide/vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { useUserStore } from '@/stores/user'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '@/utils/passwordValidation'
import { generatePixelAvatar } from '@/utils/pixelAvatar'

const userStore = useUserStore()
const avatarUploading = ref(false)
const refreshing = ref(false)
const savingField = ref('')
const editingField = ref('')
const usernameInput = ref(null)
const phoneInput = ref(null)
const userConfigRef = ref(null)
const changingPassword = ref(false)
const passwordModalVisible = ref(false)
const profileDraft = reactive({
  username: '',
  phone_number: ''
})
const passwordDraft = reactive({
  currentPassword: '',
  newPassword: '',
  confirmPassword: ''
})

const resetPasswordDraft = () => {
  passwordDraft.currentPassword = ''
  passwordDraft.newPassword = ''
  passwordDraft.confirmPassword = ''
}

const openPasswordModal = () => {
  resetPasswordDraft()
  passwordModalVisible.value = true
}

const closePasswordModal = () => {
  if (changingPassword.value) return
  passwordModalVisible.value = false
}

const avatarDefaultSrc = computed(() => (userStore.uid ? generatePixelAvatar(userStore.uid) : ''))

const userRoleText = computed(() => {
  switch (userStore.userRole) {
    case 'superadmin':
      return '超级管理员'
    case 'admin':
      return '管理员'
    case 'user':
      return '普通用户'
    default:
      return '未知角色'
  }
})

const syncProfileDraft = () => {
  profileDraft.username = userStore.username || ''
  profileDraft.phone_number = userStore.phoneNumber || ''
}

const refreshProfile = async () => {
  refreshing.value = true
  try {
    await Promise.all([userStore.getCurrentUser(), userConfigRef.value?.refresh?.()])
    syncProfileDraft()
    message.success('账户设置已刷新')
  } catch (error) {
    console.error('刷新用户信息失败:', error)
    message.error('刷新失败：' + (error.message || '请稍后重试'))
  } finally {
    refreshing.value = false
  }
}

const startFieldEdit = async (field) => {
  syncProfileDraft()
  editingField.value = field
  await nextTick()
  const inputRef = field === 'username' ? usernameInput.value : phoneInput.value
  inputRef?.focus?.()
}

const cancelField = () => {
  if (savingField.value) return
  editingField.value = ''
  syncProfileDraft()
}

const saveField = async (field) => {
  const payload = {}
  if (field === 'username') {
    const username = profileDraft.username.trim()
    if (username.length < 2 || username.length > 20) {
      message.error('用户名长度必须在 2-20 个字符之间')
      return
    }
    if (username === userStore.username) {
      cancelField()
      return
    }
    payload.username = username
  }

  if (field === 'phone_number') {
    const phoneNumber = profileDraft.phone_number.trim()
    if (phoneNumber && !validatePhoneNumber(phoneNumber)) {
      message.error('请输入正确的手机号格式')
      return
    }
    if (phoneNumber === (userStore.phoneNumber || '')) {
      cancelField()
      return
    }
    payload.phone_number = phoneNumber
  }

  savingField.value = field
  try {
    await userStore.updateProfile(payload)
    syncProfileDraft()
    editingField.value = ''
    message.success('个人资料更新成功')
  } catch (error) {
    console.error('更新个人资料失败:', error)
    message.error('更新失败：' + (error.message || '请稍后重试'))
  } finally {
    savingField.value = ''
  }
}

const handlePasswordChange = async () => {
  if (!passwordDraft.currentPassword) {
    message.error('请输入当前密码')
    return
  }
  if (!isPasswordLongEnough(passwordDraft.newPassword)) {
    message.error(`新密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`)
    return
  }
  if (passwordDraft.newPassword === passwordDraft.currentPassword) {
    message.error('新密码不能与当前密码相同')
    return
  }
  if (passwordDraft.newPassword !== passwordDraft.confirmPassword) {
    message.error('两次输入的新密码不一致')
    return
  }

  changingPassword.value = true
  let passwordChanged = false
  try {
    await userStore.changePassword({
      current_password: passwordDraft.currentPassword,
      new_password: passwordDraft.newPassword
    })
    resetPasswordDraft()
    passwordModalVisible.value = false
    passwordChanged = true
    message.success('密码修改成功，请重新登录')
    setTimeout(() => {
      userStore.logout()
      window.location.href = '/login'
    }, 800)
  } catch (error) {
    console.error('修改密码失败:', { status: error?.status ?? null })
    message.error(error?.status === 400 ? '当前密码错误' : error.message || '密码修改失败，请稍后重试')
  } finally {
    if (!passwordChanged) {
      changingPassword.value = false
    }
  }
}

const getRoleColor = (role) => {
  switch (role) {
    case 'superadmin':
      return 'var(--color-error-700)'
    case 'admin':
      return 'var(--color-primary-500)'
    case 'user':
      return 'var(--color-success-500)'
    default:
      return 'var(--gray-600)'
  }
}

const validatePhoneNumber = (phone) => {
  if (!phone) return true
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

const beforeUpload = (file) => {
  const isImage = file.type.startsWith('image/')
  if (!isImage) {
    message.error('只能上传图片文件！')
    return false
  }

  const isLt5M = file.size / 1024 / 1024 < 5
  if (!isLt5M) {
    message.error('图片大小不能超过 5MB！')
    return false
  }

  return true
}

const handleAvatarChange = async (info) => {
  if (info.file.status === 'uploading') {
    avatarUploading.value = true
    return
  }

  if (info.file.status === 'done') {
    avatarUploading.value = false
    return
  }

  try {
    avatarUploading.value = true
    await userStore.uploadAvatar(info.file.originFileObj || info.file)
    message.success('头像上传成功！')
  } catch (error) {
    console.error('头像上传失败:', error)
    message.error('头像上传失败：' + (error.message || '请稍后重试'))
  } finally {
    avatarUploading.value = false
  }
}

watch(() => [userStore.username, userStore.phoneNumber], syncProfileDraft, { immediate: true })
</script>

<style lang="less" scoped>
.account-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;

  .account-card {
    padding: 18px;
    border-radius: 12px;
    background: var(--gray-0);
    border: 1px solid var(--gray-150);
  }

  .profile-card {
    display: flex;
    flex-direction: column;
    gap: 18px;
    background: var(--gray-25);
  }

  .profile-card-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--gray-150);
  }

  .profile-card-title {
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 600;
  }

  .profile-card-description {
    margin: 4px 0 0;
    color: var(--gray-600);
    font-size: 13px;
  }

  .password-entry-button {
    flex: 0 0 auto;
  }

  .profile-summary {
    display: flex;
    align-items: stretch;
    justify-content: space-between;
    gap: 20px;

    @media (max-width: 760px) {
      flex-direction: column;
    }
  }

  .profile-left {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 18px;
    flex: 1;

    @media (max-width: 520px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .avatar-upload {
    width: 80px;
    height: 80px;
    position: relative;
    cursor: pointer;
    border-radius: 50%;
    overflow: hidden;
    flex: 0 0 auto;

    .account-avatar {
      width: 80px;
      height: 80px;
      border: 3px solid var(--gray-0);
    }

    &:hover .avatar-mask,
    &.uploading .avatar-mask {
      opacity: 1;
    }
  }

  .avatar-mask {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    color: var(--gray-0);
    font-size: 12px;
    background: rgba(0, 0, 0, 0.48);
    opacity: 0;
    transition: opacity 0.2s ease;
  }

  .profile-fields {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex: 1;
  }

  .profile-row {
    min-width: 0;
    display: grid;
    grid-template-columns: 56px minmax(0, 1fr);
    align-items: center;
    gap: 10px;
  }

  .profile-label {
    color: var(--gray-600);
    font-size: 13px;
    flex-shrink: 0;
  }

  .profile-value,
  .editable-value {
    min-width: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 24px;
    overflow: hidden;
    text-align: left;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .editable-value {
    width: fit-content;
    max-width: 100%;
    padding: 0 6px;
    margin-left: -6px;
    border: none;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;

    &:hover {
      color: var(--main-color);
      background: var(--main-5);
    }
  }

  .inline-input {
    width: min(260px, 100%);
  }

  .identity-panel {
    min-width: 220px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 12px;
    padding: 14px;
    border-radius: 10px;
    background: var(--gray-0);

    @media (max-width: 760px) {
      min-width: 0;
    }
  }

  .identity-item {
    min-width: 0;
    display: grid;
    grid-template-columns: 20px 42px minmax(0, 1fr);
    align-items: center;
    gap: 8px;
  }

  .identity-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 6px;
    background: var(--gray-50);
    color: var(--gray-500);
  }

  .mono {
    font-family: 'Monaco', 'Consolas', monospace;
  }

  .apikey-card {
    padding: 16px;
  }
}

.password-modal-content {
  .password-modal-description {
    margin: 0 0 20px;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.6;
  }

  .password-form {
    :deep(.ant-form-item:last-child) {
      margin-bottom: 16px;
    }
  }

  .password-requirement {
    padding: 10px 12px;
    border-radius: 8px;
    color: var(--gray-600);
    background: var(--gray-50);
    font-size: 12px;
    line-height: 1.5;
  }
}

:deep(.spin) {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
