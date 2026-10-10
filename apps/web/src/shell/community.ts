import LoginModal from '../ui/LoginModal.vue'
import type { ProductUiExtension } from './contracts'

const communityProductUi: ProductUiExtension = {
  extensionId: 'zy.community',
  loginModal: LoginModal,
}

export default communityProductUi
