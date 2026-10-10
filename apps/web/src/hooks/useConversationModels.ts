import { computed, ref } from 'vue'
import { fetchChatModels, type ChatModelOption } from '../api/models'
import { t, type Locale } from './i18n'

export function useConversationModels(options: {
  getMainId: () => string
  getLocale: () => Locale
  getPreferredModelId?: () => string
}) {
  const chatModels = ref<ChatModelOption[]>([])
  const selectedModelId = ref('')
  const modelLoadError = ref('')
  const modelsLoading = ref(false)

  const selectedModel = computed(() => chatModels.value.find((item) => item.id === selectedModelId.value) || null)
  const modelSelectorLabel = computed(() => {
    if (modelsLoading.value) return t('chat.models_loading')
    if (modelLoadError.value) return t('chat.models_load_failed')
    if (selectedModel.value) return selectedModel.value.displayName || selectedModel.value.modelName
    const fallback = options.getLocale() === 'en' ? 'No model has been set up' : '未配置模型'
    if (chatModels.value.length) return chatModels.value[0]?.displayName || chatModels.value[0]?.modelName || fallback
    return fallback
  })

  async function loadChatModels() {
    try {
      modelsLoading.value = true
      modelLoadError.value = ''
      const items = await fetchChatModels(options.getMainId() || 'default')
      chatModels.value = items
      const preferredModelId = options.getPreferredModelId?.() || ''
      if (preferredModelId && items.some((item) => item.id === preferredModelId)) {
        selectedModelId.value = preferredModelId
      } else if (!selectedModelId.value) {
        selectedModelId.value = items[0]?.id || ''
      } else if (!items.some((item) => item.id === selectedModelId.value)) {
        selectedModelId.value = items[0]?.id || ''
      }
    } catch (error: any) {
      chatModels.value = []
      selectedModelId.value = ''
      modelLoadError.value = String(error?.message || t('chat.models_load_failed'))
    } finally {
      modelsLoading.value = false
    }
  }

  function selectChatModel(modelId: string) {
    selectedModelId.value = modelId
  }

  return {
    chatModels,
    selectedModelId,
    modelLoadError,
    modelsLoading,
    selectedModel,
    modelSelectorLabel,
    loadChatModels,
    selectChatModel,
  }
}
