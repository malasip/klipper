#include "autoconf.h"
#include "board/armcm_boot.h"
#include "board/serial_irq.h"
#include "command.h"
#include "internal.h"
#include "sched.h"

#if CONFIG_GD32_SERIAL_USART1_PA2_PA3
	#define GPIO_Rx 			GPIO('A',3)
	#define GPIO_Tx 			GPIO('A',2)
	#define USARTx_AF 			GPIO_AF_1
	#define USARTx 				USART1
	#define USARTx_IRQn 		USART1_IRQn
	#define USARTx_PCLK			RCU_USART1	
	#define USARTx_PIN_PCLK		RCU_GPIOA
	#define USARTx_IRQHandler	USART1_IRQHandler
	DECL_CONSTANT_STR("RESERVE_PINS_serial", "PA2,PA3");
#elif CONFIG_GD32_SERIAL_USART0_PA9_PA10
	#define GPIO_Rx 			GPIO('A',10)
	#define GPIO_Tx 			GPIO('A',9)
	#define USARTx_AF 			GPIO_AF_1
	#define USARTx 				USART0
	#define USARTx_IRQn 		USART0_IRQn
	#define USARTx_PCLK			RCU_USART0
	#define USARTx_PIN_PCLK		RCU_GPIOA
	#define USARTx_IRQHandler	USART0_IRQHandler
	DECL_CONSTANT_STR("RESERVE_PINS_serial", "PA9,PA10");
#else
#error "at32 serial configuration error"
#endif

void
USARTx_IRQHandler(void)
{
    uint32_t stat = USART_STAT0(USARTx);
    if (stat & (USART_STAT0_RBNE | USART_STAT0_ORERR | USART_STAT0_FERR)) {
        uint8_t rx_data = (uint8_t)(USART_DATA(USARTx) & 0xFF);
        if (stat & USART_STAT0_RBNE)
            serial_rx_byte(rx_data);
    }

    if ((stat & USART_STAT0_TBE) && (USART_CTL0(USARTx) & USART_CTL0_TBEIE)) {
        uint8_t data = 0;
        int ret = serial_get_tx_byte(&data);
        if (ret)
            USART_CTL0(USARTx) &= ~USART_CTL0_TBEIE;
        else
            USART_DATA(USARTx) = data;
    }
}

void
serial_enable_tx_irq(void)
{
    USART_CTL0(USARTx) |= USART_CTL0_TBEIE;
}

void 
serial_init(void)
{
	uint32_t intdiv = 0U, fradiv = 0U, udiv = 0U;

	uint32_t gpio = GPIO_Rx;

	uint32_t uclk = get_pclock_frequency(0); 
			 
	uint32_t baudval = CONFIG_SERIAL_BAUD; 

#if CONFIG_MACH_GD32F303XX	

	gpio_peripheral(gpio, 1, 3);
	
#elif CONFIG_MACH_GD32E230X8

	gpio_init_af_set(gpio, USARTx_AF);

	gpio_init_mode_set(gpio,GPIO_MODE_AF, GPIO_PUPD_PULLUP);

	gpio_init_output_options_set(gpio,GPIO_OTYPE_PP);	

#endif
	
	gpio = GPIO_Tx;

#if CONFIG_MACH_GD32F303XX	

	gpio_peripheral(gpio, 3, 1);

#elif CONFIG_MACH_GD32E230X8

	gpio_init_af_set(gpio, USARTx_AF);

	gpio_init_mode_set(gpio,GPIO_MODE_AF, GPIO_PUPD_PULLUP);

	gpio_init_output_options_set(gpio,GPIO_OTYPE_PP);	

#endif

	enable_pclock(USARTx_PCLK);

	USART_CTL0(USARTx) &= ~(USART_CTL0_UEN);
    
	USART_CTL0(USARTx) &= ~USART_CTL0_WL;
    
	USART_CTL0(USARTx) |= USART_WL_8BIT;

	USART_CTL1(USARTx) &= ~USART_CTL1_STB;
    
	USART_CTL1(USARTx) |= USART_STB_1BIT;

	USART_CTL0(USARTx) &= ~(USART_CTL0_PM | USART_CTL0_PCEN);
    
	USART_CTL0(USARTx) |= USART_PM_NONE;

#if CONFIG_MACH_GD32E230X8

	if(USART_CTL0(USARTx) & USART_CTL0_OVSMOD)
	{
        /* oversampling by 8, configure the value of USART_BAUD */
        udiv = ((2U * uclk) + baudval / 2U) / baudval;
        
		intdiv = udiv & 0x0000fff0U;
        
		fradiv = (udiv>>1U) & 0x00000007U;
        
		USART_BAUD(USARTx) = ((USART_BAUD_FRADIV | USART_BAUD_INTDIV) & (intdiv | fradiv));
    }
	else
	{
        /* oversampling by 16, configure the value of USART_BAUD */
        udiv = (uclk + baudval / 2U) / baudval;
        
		intdiv = udiv & 0x0000fff0U;
        
		fradiv = udiv & 0x0000000fU;
        
		USART_BAUD(USARTx) = ((USART_BAUD_FRADIV | USART_BAUD_INTDIV) & (intdiv | fradiv));
    }

#elif CONFIG_MACH_GD32F303XX

    /* oversampling by 16, configure the value of USART_BAUD */
    udiv = (uclk + baudval / 2U) / baudval;
        
	intdiv = udiv & 0x0000fff0U;
        
	fradiv = udiv & 0x0000000fU;
        
	USART_BAUD(USARTx) = ((USART_BAUD_FRADIV | USART_BAUD_INTDIV) & (intdiv | fradiv));

#endif

	USART_CTL0(USARTx) &= ~USART_CTL0_REN;

	USART_CTL0(USARTx) |= USART_RECEIVE_ENABLE;

	USART_CTL0(USARTx) &= ~USART_CTL0_TEN;

	USART_CTL0(USARTx) |= USART_TRANSMIT_ENABLE;

	USART_CTL0(USARTx) |= USART_CTL0_UEN;

  	armcm_enable_irq(USARTx_IRQHandler,USARTx_IRQn,0);

	USART_REG_VAL(USARTx, USART_INT_RBNE) |= BIT(USART_BIT_POS(USART_INT_RBNE));
}		

DECL_INIT(serial_init);
	

